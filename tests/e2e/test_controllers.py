"""The controller requirement, against a real daemon and real devices.

Two things may drive this picker: the keyboard, and a controller padmap has
published. Nothing else -- and a pad becomes one of padmap's by being picked
up and held, from wherever the picker happens to be, never from a screen
somebody had to find first.

Every test here runs the picker's own modules -- `gotg_ui.padmap`,
`assign.attend`, `gotg_ui.pads` -- against a padmap daemon of its own and
controllers made out of /dev/uinput. Nothing is mocked, deliberately: every
bug this suite was written after lived in the gap between what the code
believed about SDL and what SDL does. A mock would have agreed with the code
and shipped the bug.

Run them with `nix run .#test-controllers`, which brings padmap and a pygame.
GOTG_E2E_REQUIRE=1 turns "cannot run here" into a failure.
"""

from __future__ import annotations

import time

import pytest
from fakepad import BTN_SOUTH, BTN_START, FakePad, kernel_names

from gotg_ui import pads
from gotg_ui.assign import Session, Watch, attend
from gotg_ui.padmap import Padmap
from gotg_ui.padstrip import next_seat, seats, strip_status

# Long enough for a daemon scan (1s), a hold (0.25s) and a republish, with the
# slack a loaded machine needs. Tests wait for a condition, not for this.
PATIENCE = 12.0


class Picker:
    """The picker's controller half, one frame at a time.

    The same three calls the event loop makes, in the same order: attend to
    padmap, apply what it said, then read the pads. What is not here is the
    grid, the art and the window -- none of which has an opinion about who may
    press a button.
    """

    def __init__(self, socket_path, sdl):
        self.sdl = sdl
        self.padmap = Padmap(socket_path)
        assert self.padmap.connect(), "the picker could not reach the test daemon"
        self.seating = Session()
        self.watch = Watch()
        self.sticks = pads.init()
        self.sent: list[dict] = []
        self.pressed: list[str] = []
        self.progress_seen = 0.0

    def frame(self) -> None:
        listen = attend(self.padmap, self.seating, self.watch)
        if listen is not None:
            self.sent.append(listen)
            self.padmap.send(listen)
        self.progress_seen = max(self.progress_seen, self.seating.view.progress)

        for event in self.sdl.event.get():
            # Exactly what app.py does with a pad arriving or leaving: padmap
            # publishing a clone is a device this program has never opened.
            if event.type == self.sdl.JOYDEVICEADDED:
                self.sticks.add(event.device_index)
                continue
            if event.type == self.sdl.JOYDEVICEREMOVED:
                self.sticks.remove(event.instance_id)
                continue
            name = pads.button(event)
            if name is not None:
                self.pressed.append(name)

    def run(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.frame()
            time.sleep(0.02)

    def until(self, done, seconds: float = PATIENCE) -> bool:
        """Frames until something is true, or the patience runs out."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.frame()
            if done(self):
                return True
            time.sleep(0.02)
        return False

    @property
    def players(self) -> list[dict]:
        return self.padmap.players

    def seated(self, player: int) -> bool:
        return any(p.get("player") == player for p in self.players)

    def claim(self, pad: FakePad, player: int, tries: int = 3) -> bool:
        """Hold until this pad is seated as `player`, the way a person does.

        Once is usually enough. It is not when the hold lands while the daemon
        is republishing the *previous* seat and has not yet reopened the other
        pads for seating -- the press happens before anything is reading, and
        a press nobody read cannot be claimed however long it is held. A
        person just holds again; so does this. Single-pad tests still hold
        once, so that path stays strict.
        """
        for _ in range(tries):
            pad.hold(BTN_SOUTH, 0.7)
            if self.until(lambda p: p.seated(player), seconds=4.0):
                return True
        return False

    def close(self) -> None:
        self.padmap.close()


# --- the requirement --------------------------------------------------------


def test_the_picker_opens_with_no_controllers(daemon, sdl):
    """Nothing is seated until somebody claims a seat, however much is plugged in."""
    with FakePad("E2E Xbox Pad"):
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)

        assert picker.players == [], "a pad was seated without anybody holding anything"
        assert seats(picker.players) == []
        assert strip_status(picker.padmap.status_word, 0) == "hold a button on a controller"
        picker.close()


def test_a_pad_padmap_has_not_published_cannot_move_the_picker(daemon, sdl):
    """The requirement, stated as its failure: an unclaimed pad moves nothing.

    The pad is real, connected, and SDL is delivering its presses -- this is
    not a test that nothing happened, it is a test that the picker refused
    what did.
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)

        saw_sdl_events = False
        for _ in range(4):
            pad.tap(BTN_SOUTH)
            time.sleep(0.1)
            for event in sdl.event.get():
                if event.type in (sdl.CONTROLLERBUTTONDOWN, sdl.JOYBUTTONDOWN):
                    saw_sdl_events = True
                    assert pads.button(event) is None, (
                        "an unpublished pad drove the picker -- this is the whole requirement"
                    )
        assert saw_sdl_events, "SDL never saw the pad at all, so nothing was proven"
        picker.close()


def test_holding_a_button_claims_player_one_from_wherever_the_picker_is(daemon, sdl):
    """No screen to find, no session, no pairing detour: hold a button, be player one."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        assert picker.sent and picker.sent[0]["cmd"] == "seating", (
            "the picker never asked padmap to listen for a hold"
        )

        pad.down(BTN_SOUTH)
        picker.run(0.7)
        pad.up(BTN_SOUTH)
        assert picker.until(lambda p: p.seated(1)), "holding a button seated nobody"

        assert picker.progress_seen > 0, "nothing filled the ring while the button was held"
        assert "padmap Player 1" in kernel_names(), "padmap seated a player but published no pad"
        assert next_seat(picker.players) == 2
        picker.close()


def test_and_that_controller_then_drives_the_picker(daemon, sdl):
    """The other half: once padmap has published it, its presses are the picker's."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1)), "holding a button seated nobody"
        picker.run(1.0)          # let SDL announce the clone; the loop opens it

        picker.pressed.clear()
        pad.tap(BTN_SOUTH)
        picker.run(0.6)
        assert picker.pressed == ["a"], (
            f"one press on a seated controller should be one A, got {picker.pressed}"
        )

        picker.pressed.clear()
        pad.tap(BTN_START)
        picker.run(0.6)
        assert picker.pressed == ["start"]
        picker.close()


def test_a_second_controller_becomes_player_two(daemon, sdl):
    """Which is the numbering the games are bound against: player N is the Nth to hold."""
    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)

        assert picker.claim(first, 1), "the first pad took no seat"
        assert picker.claim(second, 2), "the second pad took no seat"

        numbered = sorted(p["player"] for p in picker.players)
        assert numbered == [1, 2]
        nodes = {p["player"]: p.get("node") for p in picker.players}
        assert nodes[1] != nodes[2], "two seats, one controller -- padmap seated the same pad twice"
        assert {"padmap Player 1", "padmap Player 2"} <= set(kernel_names())
        picker.close()


def test_the_picker_never_opens_a_session_of_its_own(daemon, sdl):
    """A session grabs every pad and takes the screen. That is the detour, and it is gone."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        picker.until(lambda p: p.seated(1))
        picker.run(0.5)

        assert all(command["cmd"] == "seating" for command in picker.sent), (
            f"the picker sent something other than seating: {picker.sent}"
        )
        daemon.drain(0.5)
        assert "assigning" not in daemon.states, (
            "a session was opened -- the pairing screen is back"
        )
        picker.close()


def test_with_no_padmap_at_all_a_pad_still_cannot_move_the_picker(sdl):
    """The bug as it was seen: the picker opened, no daemon anywhere, and an
    Xbox pad that padmap had never heard of moved the cursor.

    No `daemon` fixture on purpose. Nothing is running, nothing is on the
    socket, and the pad is refused anyway -- because the rule is about the
    pad, not about padmap's state.
    """
    with FakePad("E2E Xbox Pad") as pad:
        sticks = pads.init()
        # SDL announces the pad a moment after it exists, and the picker opens
        # it on that announcement; a test that cleared the queue here threw
        # the announcement away and then proved nothing.
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            for event in sdl.event.get():
                if event.type == sdl.JOYDEVICEADDED:
                    sticks.add(event.device_index)
            time.sleep(0.02)
        saw = False
        for _ in range(4):
            pad.tap(BTN_SOUTH)
            time.sleep(0.1)
            for event in sdl.event.get():
                if event.type == sdl.JOYDEVICEADDED:
                    sticks.add(event.device_index)
                elif event.type in (sdl.CONTROLLERBUTTONDOWN, sdl.JOYBUTTONDOWN):
                    saw = True
                    assert pads.button(event) is None, "a pad padmap never published drove the picker"
                elif event.type == sdl.JOYHATMOTION:
                    assert pads.direction(event) is None
        assert saw, "SDL never delivered the press, so nothing was proven"


def test_the_keyboard_is_never_filtered(sdl):
    """Two things drive the picker, and the other one is the keyboard.

    The filter is only ever asked about pads: a key is not a pad event and
    cannot be swallowed by it, whatever padmap is doing.
    """
    key = sdl.event.Event(sdl.KEYDOWN, {"key": sdl.K_a, "unicode": "a", "mod": 0})
    assert pads.button(key) is None
    assert pads.direction(key) is None


def _socket(daemon):
    """Where this test's daemon listens, as the picker's client wants it."""
    from pathlib import Path

    return Path(daemon.path)


# --- pairing is on the menu, and the menu's top bar says so -----------------


def test_pairing_happens_on_the_menu_and_shows_in_the_top_bar(daemon, sdl):
    """Not the controller screen. The grid, the strip along the top, a hold.

    Three things are asserted against the picker's own models: no assignment
    screen ever opened, the strip went from "no controllers" to one seat, and
    the words at its right-hand end changed from an instruction to a fact.
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)

        assert not picker.seating.open, "the controller screen opened on its own"
        assert seats(picker.players) == []
        assert strip_status(picker.padmap.status_word, 0) == "hold a button on a controller"

        pad.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1)), "holding a button seated nobody"
        picker.run(0.3)

        assert not picker.seating.open, "the hold dragged the picker onto the controller screen"
        assert [player for player, _ in seats(picker.players)] == [1], "the top bar shows no seat"
        assert strip_status(picker.padmap.status_word, 1) == "controllers assigned"
        assert next_seat(picker.players) == 2, "the top bar should be ready for player two"
        picker.close()


def test_a_controller_can_still_join_after_the_picker_has_left_for_a_game(daemon, sdl):
    """Any time: the grid, a game, anything. The picker is gone and a hold still seats you.

    The picker is the client that asked padmap to listen; a game has no
    client at all. So the picker disconnects, the observer disconnects, and a
    pad is held with nobody on the socket.
    """
    from conftest import Daemon

    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        first.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1))
        picker.close()          # the picker execs into the game
        daemon.close()          # and nothing else is listening
        time.sleep(0.5)

        second.hold(BTN_SOUTH, 0.7)
        time.sleep(2.0)

        later = Daemon(daemon.path)
        try:
            numbered = sorted(p["player"] for p in later.players)
        finally:
            later.close()
        assert numbered == [1, 2], f"a pad held mid-game took no seat: {numbered}"
        assert "padmap Player 2" in kernel_names()


# --- every session starts unseated ------------------------------------------


def test_a_new_session_opens_with_nobody_seated_whatever_padmap_remembers(daemon, sdl, monkeypatch):
    """Open the picker: nobody is seated until somebody holds a button.

    The daemon the fixture started remembers a seat. The picker's own
    `ensure_daemon` -- fresh, following this pid -- replaces it with one that
    does not, which is what padmap built for docs/requests/session-daemon.md.
    """
    import os
    import signal

    from gotg_ui.padmap import ensure_daemon

    with FakePad("E2E Xbox Pad") as pad:
        earlier = Picker(_socket(daemon), sdl)
        earlier.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert earlier.until(lambda p: p.seated(1))
        earlier.close()

        # The picker opening, exactly as app.py does it, against this daemon.
        for key, value in daemon.env.items():
            monkeypatch.setenv(key, value)
        # Set rather than deleted, so monkeypatch has something to restore:
        # ensure_daemon writes "1" here on success, and a delenv of an absent
        # key would leave that for every test after this one.
        monkeypatch.setenv("PADMAP_SKIP_DAEMON_CHECK", "0")
        assert ensure_daemon(fresh=True, follow=os.getpid()) is None
        for _ in range(40):
            if os.path.exists(daemon.path):
                break
            time.sleep(0.25)

        picker = Picker(_socket(daemon), sdl)
        try:
            picker.run(1.5)
            assert picker.players == [], (
                f"the session opened with seats already taken: {picker.players}"
            )
            assert picker.padmap.state.get("following") == os.getpid(), (
                "the daemon is not following the picker"
            )
            # And the seat is still there for the taking: the same pad, held
            # again, is player one again.
            pad.hold(BTN_SOUTH, 0.7)
            assert picker.until(lambda p: p.seated(1)), "the fresh daemon seated nobody"
        finally:
            fresh_pid = picker.padmap.state.get("pid")
            picker.close()
            if isinstance(fresh_pid, int):
                try:
                    os.kill(fresh_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def test_a_game_launch_begins_with_a_hold_whatever_was_seated(daemon, sdl):
    """Launch a game: the gate forgets what the daemon remembers, then asks for a hold.

    This is `gotg-seat`'s model -- gate.decide and gate.apply -- driven
    against the real daemon, after a picker has seated somebody on it.
    """
    from gotg_ui.gate import SEATING, Gate, apply, decide

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1))
        picker.close()          # execvp into the game; the gate is next

        client = Padmap(_socket(daemon))
        assert client.connect()
        # As seat.py does: the first state before the first decision, or a
        # gate that has heard nothing asks for a hold with somebody seated.
        for _ in range(100):
            for _event in client.poll():
                pass
            if client.state:
                break
            time.sleep(0.05)
        assert client.state, "the daemon never answered status"
        gate = Gate(platform="gamecube")
        gate = apply(gate, client.state)
        sent: list[dict] = []

        def step(seconds: float) -> None:
            nonlocal gate
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                for event in client.poll():
                    gate = apply(gate, event)
                gate, command = decide(gate)
                if command is not None:
                    sent.append(command)
                    client.send(command)
                time.sleep(0.02)

        try:
            step(2.0)
            assert sent and sent[0] == {"cmd": "unseat"}, f"the gate's first word was not unseat: {sent}"
            assert {"cmd": "begin", "players": 4} in sent, f"no hold was asked for: {sent}"
            assert gate.state == SEATING
            assert gate.seated == 0, "the gate is asking for a hold with somebody still seated"
            # The clone goes when the kernel gets round to it, and the name
            # is global: a daemon the previous test is still tearing down
            # may hold one too for a moment.
            end = time.monotonic() + 4.0
            while time.monotonic() < end and "padmap Player 1" in kernel_names():
                step(0.2)
            assert "padmap Player 1" not in kernel_names(), "the old seat's clone is still published"

            pad.hold(BTN_SOUTH, 0.7)
            step(2.0)
            assert gate.seated == 1, "the hold in front of the gate seated nobody"
            assert [s.player for s in gate.seats] == [1]
        finally:
            client.send({"cmd": "cancel"})
            client.close()


# --- a controller that is also a keyboard ------------------------------------


def _reader(path: str):
    """The compositor, as far as a grab is concerned: another fd on the node."""
    import os

    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def _arrived(fd: int, seconds: float = 0.6) -> bool:
    """Whether any event reaches this fd in this long."""
    import os

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if os.read(fd, 4096):
                return True
        except BlockingIOError:
            pass
        time.sleep(0.02)
    return False


def test_a_controllers_keyboard_and_mouse_never_reach_the_compositor(sdl):
    """The bug as it was seen the second time: the joystick rule held, and a
    Steam Controller in lizard mode moved the cursor anyway -- as arrow keys.

    Pygame under the dummy driver has no keyboard path, so this is proven
    where it happens: a second reader on the node, standing in for the
    compositor, receives nothing while the picker holds the grab, and
    everything once it lets go. A real keyboard next to it is never held.
    """
    import os

    from fakepad import KEY_RIGHT, event_node

    from gotg_ui.hush import Hush

    with (
        FakePad("E2E Xbox Pad", phys="usb-e2e/input0") as pad,
        FakePad("E2E Xbox Pad Keyboard", phys="usb-e2e/input0", keyboard=True) as lizard,
        FakePad("E2E Real Keyboard", 0x1D6B, 0x0001, 1, phys="usb-desk/input0", keyboard=True) as real,
    ):
        del pad  # it is there to make the keyboard a controller's; nothing presses it
        time.sleep(0.5)
        lizard_node = event_node("E2E Xbox Pad Keyboard")
        real_node = event_node("E2E Real Keyboard")
        assert lizard_node and real_node, "the kernel never listed the keyboards"

        compositor = _reader(lizard_node)
        desk = _reader(real_node)
        try:
            hush = Hush()
            held = hush.refresh()
            assert os.path.basename(lizard_node) in held, f"the controller's keyboard was not held: {held}"
            assert os.path.basename(real_node) not in held, "a real keyboard was grabbed"

            lizard.tap(KEY_RIGHT)
            assert not _arrived(compositor), "a key from the controller's keyboard reached the compositor"
            real.tap(KEY_RIGHT)
            assert _arrived(desk), "the real keyboard was silenced"

            hush.release()
            lizard.tap(KEY_RIGHT)
            assert _arrived(compositor), "the grab outlived the picker"
        finally:
            os.close(compositor)
            os.close(desk)


# --- the first window of a launch ---------------------------------------------
#
# `gotg-seat` as the launcher runs it: a process of its own, against the
# daemon. Two routes reach it. From the grid the picker execvps into the
# launcher, so the pid is the picker's and the daemon is kept, seats and all,
# for the gate to forget. From Steam there is no picker: the gate starts a
# daemon of its own, fresh, following itself.


def _seat_process(daemon, extra: dict, root: str):
    import os
    import subprocess
    import sys

    env = {**os.environ, **daemon.env}
    # The latch padmap's own client sets after a successful ensure-daemon.
    # An earlier test in this process may have left it in os.environ -- a
    # monkeypatched delenv of a key that was absent records nothing to
    # restore -- and a gate that inherits it never starts its own daemon.
    env.pop("PADMAP_SKIP_DAEMON_CHECK", None)
    env.update(extra)
    env.setdefault("SDL_VIDEODRIVER", "dummy")
    env.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    env.setdefault("GOTG_CONFIG", os.path.join(root, "config"))
    env.setdefault("PADMAP_NO_AUTOSETUP", "1")
    return subprocess.Popen(
        [sys.executable, "-m", "gotg_ui.seat", "--platform", "gamecube", "--title", "E2E launch"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _root() -> str:
    import os

    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_a_launch_from_the_grid_meets_the_gate_first_which_forgets_the_seat(daemon, sdl):
    """The picker route: the same daemon, and its seat is the first thing to go."""
    import signal

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1))
        picker.close()
        daemon.drain(0.5)

        # PADMAP_SKIP_DAEMON_CHECK is what the picker exports before it execvps,
        # so the launcher does not ask for a daemon it already has.
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            removed = daemon.wait_for("controller", seconds=8.0)
            while removed is not None and removed.get("reason") != "unseated":
                removed = daemon.wait_for("controller", seconds=4.0)
            assert removed is not None, "the gate never told the daemon to forget the seat"
            assigning = None
            end = time.monotonic() + 8.0
            while time.monotonic() < end and assigning is None:
                for event in daemon.drain(0.2):
                    if event.get("event") == "state" and event.get("state") == "assigning":
                        assigning = event
            assert assigning is not None, "the gate never opened a session to take a seat in"
            assert assigning.get("players") == [], "the gate is asking for a hold with somebody seated"
            assert seat.poll() is None, "the gate exited before anybody held a button"
        finally:
            seat.send_signal(signal.SIGTERM)
            seat.wait(timeout=5)


def test_a_launch_from_steam_meets_the_gate_first_on_a_daemon_of_its_own(daemon, sdl):
    """The Steam route: no picker, so the gate starts a fresh daemon following itself,
    and that daemon ends with it."""
    import os
    import signal

    from conftest import Daemon

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1))
        picker.close()
        daemon.close()          # nothing of the picker's survives a Steam launch

        seat = _seat_process(daemon, {}, _root())
        later = None
        try:
            # ensure-daemon replaces the fixture's daemon: the socket goes and
            # comes back belonging to a daemon that follows the gate.
            state = None
            end = time.monotonic() + 15.0
            while time.monotonic() < end and state is None:
                time.sleep(0.25)
                if not os.path.exists(daemon.path):
                    continue
                try:
                    later = Daemon(daemon.path)
                except OSError:
                    continue
                # Daemon() has already read the greeting and the answer to
                # `status` into `seen`; the state is there, not in a later drain.
                for event in later.seen + later.drain(0.5):
                    if event.get("event") == "state" and event.get("following") == seat.pid:
                        state = event
                if state is None:
                    later.close()
                    later = None
            if state is None:
                log = os.path.join(daemon.env["XDG_RUNTIME_DIR"], "padmap", "padmap.log")
                tail = open(log).read()[-1500:] if os.path.exists(log) else "(no daemon log)"
                seat.send_signal(signal.SIGTERM)
                _, err = seat.communicate(timeout=5)
                raise AssertionError(
                    f"no daemon following the gate (pid {seat.pid}) ever appeared\n"
                    f"--- gotg-seat stderr ---\n{err}\n--- padmap.log ---\n{tail}"
                )
            assert state.get("players") == [], "the Steam route started with yesterday's seat"
            end = time.monotonic() + 6.0
            while time.monotonic() < end and state.get("state") != "assigning":
                for event in later.drain(0.2):
                    if event.get("event") == "state":
                        state = event
            assert state.get("state") == "assigning", "the gate never asked for a hold"
            assert seat.poll() is None
        finally:
            if later is not None:
                later.close()
            seat.send_signal(signal.SIGTERM)
            seat.wait(timeout=5)
        # And the daemon it started goes with it.
        end = time.monotonic() + 4.0
        while time.monotonic() < end and os.path.exists(daemon.path):
            time.sleep(0.25)
        assert not os.path.exists(daemon.path), "the gate's daemon outlived the gate"


def test_with_no_padmap_the_gate_still_opens_a_window_before_the_game(tmp_path):
    """Never silent. The window says why and counts down; the game is not
    started behind somebody's back with nothing to play it with."""
    import os
    import shutil
    import subprocess
    import sys

    padmap = shutil.which("padmap")
    without = [d for d in os.environ.get("PATH", "").split(":") if not padmap or os.path.dirname(padmap) != d]
    env = {
        **os.environ,
        "PATH": ":".join(without),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        "SDL_VIDEODRIVER": "dummy",
        "PYGAME_HIDE_SUPPORT_PROMPT": "1",
        "GOTG_CONFIG": os.path.join(_root(), "config"),
        "GOTG_SEAT_COUNTDOWN": "2",
    }
    env.pop("PADMAP_SKIP_DAEMON_CHECK", None)
    (tmp_path / "run").mkdir()
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", "gotg_ui.seat", "--platform", "gamecube", "--title", "E2E launch"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    took = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    assert took >= 1.5, f"the gate skipped itself in {took:.2f}s -- no window, no countdown"
    assert "padmap" in done.stderr


# --- the keyboard as a player -------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="padmap has no seat_keyboard yet; see docs/requests/keyboard-as-a-player.md",
)
def test_the_keyboard_takes_a_seat_when_asked(daemon, sdl):
    """Space held on the grid ends in this command; padmap should answer with
    a seat named Keyboard, icon keyboard, in the next free slot.

    The hold itself is timed by the picker and tested in tests/ui; what is
    proven here is the daemon's half, strictly, so the day it exists this
    fails the other way until the marker comes off.
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1))

        picker.padmap.seat_keyboard()
        seated = picker.until(
            lambda p: any(pl.get("player") == 2 and pl.get("icon") == "keyboard" for pl in p.players),
            seconds=4.0,
        )
        assert seated, f"padmap seated no keyboard: {picker.players}"
        picker.close()


# --- the game waits to be told ---------------------------------------------------


# What the fake pad can press for each control padmap's wizard may ask for,
# by the control names the gamecube layout uses. Anything absent is skipped,
# which is what a person does with a control their pad does not have.
WIZARD_BUTTONS = {
    "a": BTN_SOUTH, "b": 0x131, "x": 0x134, "y": 0x133,
    "leftshoulder": 0x136, "rightshoulder": 0x137, "start": BTN_START,
}


def test_the_game_waits_until_somebody_holds_a_button_again(daemon, sdl):
    """Seated and mapped is not "go". The gate sits on "hold a button to start"
    until padmap's confirm hold completes, and only then does `accepted` arrive.

    The whole gate, against the real daemon: unseat nothing, hold, walk the
    wizard with the fake pad's own buttons, then prove the game does *not*
    start on its own, then ready up.
    """
    from gotg_ui.gate import READYING, Gate, apply, decide

    with FakePad("E2E Xbox Pad") as pad:
        client = Padmap(_socket(daemon))
        assert client.connect()
        for _ in range(100):
            for _event in client.poll():
                pass
            if client.state:
                break
            time.sleep(0.05)
        gate = Gate(platform="gamecube")
        gate = apply(gate, client.state)
        sent: list[dict] = []
        seen: list[str] = []
        t0 = time.monotonic()

        def step(seconds: float) -> None:
            nonlocal gate
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                for event in client.poll():
                    kind = event.get("event", "?")
                    if kind == "mapping":
                        seen.append(
                            f"{time.monotonic() - t0:.2f} mapping {event.get('control')} "
                            f"done={event.get('done')} conflict={event.get('conflict')!r}"
                        )
                    elif kind == "finish":
                        seen.append(f"{time.monotonic() - t0:.2f} finish {event.get('frac')}")
                    elif kind != "progress":
                        seen.append(f"{time.monotonic() - t0:.2f} {kind}")
                    gate = apply(gate, event)
                    if event.get("event") == "mapping" and not event.get("done"):
                        # A beat between the step and the answer, as a person
                        # leaves one. padmap debounces each pad: a press 170 ms
                        # after the last release was not a press to it -- the
                        # release arrived (`finish 0.0`), the capture never did,
                        # and the run stood still on the second control.
                        time.sleep(0.4)
                        control = str(event.get("control") or "")
                        button = WIZARD_BUTTONS.get(control)
                        if button is not None:
                            seen.append(f"{time.monotonic() - t0:.2f} TAP {control}")
                            pad.tap(button, hold=0.12)
                        else:
                            seen.append(f"{time.monotonic() - t0:.2f} SKIP {control}")
                            client.send({"cmd": "skip_control"})
                gate, command = decide(gate)
                if command is not None:
                    sent.append(command)
                    client.send(command)
                time.sleep(0.02)

        try:
            step(1.5)
            assert {"cmd": "begin", "players": 4} in sent
            pad.hold(BTN_SOUTH, 0.7)
            end = time.monotonic() + 30.0
            while time.monotonic() < end and gate.state != READYING and not gate.done:
                step(0.3)
            story = "\n".join(seen[-30:])
            assert gate.state == READYING, f"never reached ready-up: state={gate.state} sent={sent}\n{story}"

            # And stays there. Two seconds of nothing: no accept sent, no
            # accepted received, the game not started.
            before = len(sent)
            step(2.0)
            assert gate.state == READYING and not gate.done, "the game started on nobody's say-so"
            assert not any(c.get("cmd") == "accept" for c in sent[before:]), "the gate sent accept by itself"
            assert not any(x.endswith(" accepted") for x in seen), "padmap accepted with nobody holding anything"

            # The ready-up: a longer hold on the seated pad. padmap's confirm
            # is 0.7 s; the daemon accepts when it completes.
            pad.hold(BTN_SOUTH, 1.2)
            end = time.monotonic() + 8.0
            while time.monotonic() < end and not gate.done:
                step(0.2)
            story = "\n".join(seen[-10:])
            assert gate.done and gate.state == "ready", f"the ready-up hold did not start the game:\n{story}"
            assert any(x.endswith(" accepted") for x in seen)
        finally:
            if not gate.done:
                client.send({"cmd": "cancel"})
            client.close()


# --- a full second, on a fresh press ------------------------------------------


def test_the_gate_holds_the_door_until_a_fresh_one_second_hold(daemon, sdl):
    """The gate as the launcher runs it, against the daemon: the seat hold that
    runs straight into padmap's confirm must not also start the game. After
    `accepted` the process stays; it goes only when the pad lets go and holds
    again for a second.
    """
    import signal

    with FakePad("E2E Xbox Pad") as pad:
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            # The gate opens a session; one long press seats the pad and runs
            # on into the confirm. Answer the wizard in between.
            assert daemon.wait_for("state", seconds=8.0) is not None
            end = time.monotonic() + 8.0
            while time.monotonic() < end and "assigning" not in daemon.states:
                daemon.drain(0.2)
            assert "assigning" in daemon.states, "the gate opened no session"
            pad.hold(BTN_SOUTH, 0.7)
            accepted = None
            mapped = False
            confirmed_at = None
            end = time.monotonic() + 40.0
            while time.monotonic() < end and accepted is None:
                for event in daemon.drain(0.2):
                    kind = event.get("event")
                    if kind == "mapping" and not event.get("done"):
                        time.sleep(0.4)
                        button = WIZARD_BUTTONS.get(str(event.get("control") or ""))
                        if button is not None:
                            pad.tap(button, hold=0.12)
                        else:
                            daemon.send({"cmd": "skip_control"})
                    elif kind == "mapping" and event.get("done"):
                        mapped = True
                    elif kind == "accepted":
                        accepted = event
                # Seated and mapped: the ready-up is padmap's confirm, a
                # longer hold on the seated pad, which accepts on its own.
                if mapped and accepted is None and confirmed_at is None:
                    time.sleep(0.5)
                    pad.hold(BTN_SOUTH, 1.0)
                    confirmed_at = time.monotonic()
            assert accepted is not None, f"padmap never accepted: mapped={mapped} states={daemon.states[-5:]}"

            # The moment this launch used to start the game. It must not.
            time.sleep(2.5)
            assert seat.poll() is None, "the gate started the game right after accept"

            # A fresh press, held for a second, is the go.
            pad.hold(BTN_SOUTH, 1.3)
            end = time.monotonic() + 4.0
            while time.monotonic() < end and seat.poll() is None:
                time.sleep(0.1)
            assert seat.poll() == 0, "a full second's hold did not start the game"
        finally:
            if seat.poll() is None:
                seat.send_signal(signal.SIGTERM)
                seat.wait(timeout=5)
