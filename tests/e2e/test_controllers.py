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

import struct
import time

import pytest
from fakepad import ABS_X, BTN_SOUTH, BTN_START, FakePad, event_node, kernel_names

from gotg_ui import pads
from gotg_ui.assign import Session, Watch, attend
from gotg_ui.padmap import Padmap
from gotg_ui.padstrip import next_seat, seats, strip_status

# Long enough for a daemon scan (1s), a hold (0.25s) and a republish, with the
# slack a loaded machine needs. Tests wait for a condition, not for this.
PATIENCE = 12.0

# Long enough to claim a seat, whatever the picker asks padmap for. It used to
# be a flat 0.7 s, which was comfortably past padmap's own quarter second --
# and then the picker started asking for a second and a half, and every test
# that seated a pad stopped seating one. A hold is as long as the thing being
# tested says it is.
from gotg_ui.gate import PAIR_HOLD  # noqa: E402 - beside the constant it feeds

PAIR = PAIR_HOLD + 0.6


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
            # Frames while the button is down, not only after: `progress` is
            # what fills the ring and it arrives during the hold. A helper
            # that slept through it left the picker with nothing to draw and
            # the test with nothing to prove.
            pad.down(BTN_SOUTH)
            self.run(PAIR)
            pad.up(BTN_SOUTH)
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
        assert strip_status(picker.padmap.status_word, 0) == "hold a button on a controller, or space"
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

        # Patiently: while seating is open the daemon spends its loop
        # rescanning, and a hold can reach nobody. See
        # docs/requests/seating-costs-the-game-its-input.md.
        assert picker.claim(pad, 1), "holding a button seated nobody"

        assert picker.progress_seen > 0, "nothing filled the ring while the button was held"
        assert "padmap Player 1" in kernel_names(), "padmap seated a player but published no pad"
        assert next_seat(picker.players) == 2
        picker.close()


def test_and_that_controller_then_drives_the_picker(daemon, sdl):
    """The other half: once padmap has published it, its presses are the picker's."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
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
        pad.hold(BTN_SOUTH, PAIR)
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
        assert strip_status(picker.padmap.status_word, 0) == "hold a button on a controller, or space"

        pad.hold(BTN_SOUTH, PAIR)
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
        first.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.close()          # the picker execs into the game
        daemon.close()          # and nothing else is listening
        time.sleep(0.5)

        second.hold(BTN_SOUTH, PAIR)
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
        pad.hold(BTN_SOUTH, PAIR)
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
            pad.hold(BTN_SOUTH, PAIR)
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
        pad.hold(BTN_SOUTH, PAIR)
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
            assert {"cmd": "seating", "open": True, "players": 4, "hold": PAIR_HOLD} in sent, (
                f"no hold was listened for: {sent}"
            )
            assert not any(c.get("cmd") == "begin" for c in sent), "the gate opened a session"
            assert gate.state == SEATING
            assert gate.seated == 0, "the gate is asking for a hold with somebody still seated"
            # The clone goes when the kernel gets round to it, and the name
            # is global: a daemon the previous test is still tearing down
            # may hold one too for a moment.
            end = time.monotonic() + 4.0
            while time.monotonic() < end and "padmap Player 1" in kernel_names():
                step(0.2)
            assert "padmap Player 1" not in kernel_names(), "the old seat's clone is still published"

            pad.hold(BTN_SOUTH, PAIR)
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
    # The shipped hold is three seconds. Most of these tests are about what a
    # hold *means*, not how long it is, and paying three seconds a press
    # across the suite bought nothing -- so they ask for one second, and
    # `test_the_go_is_the_shipped_three_second_hold` runs the real length.
    env.setdefault("GOTG_SEAT_GO_HOLD", "1.0")
    env.setdefault("GOTG_SEAT_REBIND_HOLD", "1.0")
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
    """The picker route: the same daemon, its seat is the first thing to go,
    and a hold in front of the gate takes it back -- with no session."""
    import signal

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
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
            end = time.monotonic() + 4.0
            while time.monotonic() < end and daemon.players:
                daemon.drain(0.2)
            assert daemon.players == [], "the gate is asking for a hold with somebody seated"
            assert "assigning" not in daemon.states, "the gate opened a session; a pad switched on now could not join"

            time.sleep(1.0)                 # the gate's seating command lands
            pad.hold(BTN_SOUTH, PAIR)
            claimed = daemon.wait_for("claim", seconds=8.0)
            assert claimed is not None and claimed.get("player") == 1, "a hold in front of the gate seated nobody"
            assert seat.poll() is None, "the gate exited before anybody was ready"
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
        pad.hold(BTN_SOUTH, PAIR)
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
            time.sleep(1.0)                 # the gate's seating command lands
            pad.hold(BTN_SOUTH, PAIR)
            claimed = later.wait_for("claim", seconds=8.0)
            assert claimed is not None and claimed.get("player") == 1, "a hold in front of the gate seated nobody"
            assert "assigning" not in later.states, "the gate opened a session"
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
    import subprocess
    import sys

    # Every directory with a padmap in it, not just the first: the runner puts
    # the pinned one on PATH and the dev shell has its own, and once the pin
    # moved those were two store paths. Stripping one left the other, the gate
    # found padmap after all, and sat waiting for a hold nobody was there to
    # give -- a test that had been passing for the wrong reason all along.
    without = [
        d for d in os.environ.get("PATH", "").split(":")
        if not os.access(os.path.join(d, "padmap"), os.X_OK)
    ]
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


def test_the_keyboard_takes_a_seat_when_asked(daemon, sdl):
    """Space held on the grid ends in this command, and padmap answers with a
    seat named Keyboard, icon keyboard, in the next free slot.

    The hold itself is timed by the picker and tested in tests/ui; this is
    the daemon's half. It was a strict xfail against
    docs/requests/keyboard-as-a-player.md until padmap answered it, which is
    how the marker came off: the suite failed for passing.
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
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


def test_the_gate_reaches_ready_with_no_session_and_sends_no_accept(daemon, sdl):
    """The whole gate, against the real daemon, as its model runs it: listen,
    hold, walk the wizard with the fake pad's own buttons, ready. Nothing
    grabbed, no `begin`, no `accept` -- the door in seat.py is what starts
    the game, and it is tested on its own."""
    from gotg_ui.gate import READY, Gate, apply, decide

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
                    elif kind != "progress":
                        seen.append(f"{time.monotonic() - t0:.2f} {kind}")
                    gate = apply(gate, event)
                    if event.get("event") == "mapping" and not event.get("done"):
                        # A beat between the step and the answer, as a person
                        # leaves one. padmap debounces each pad: a press 170 ms
                        # after the last release was not a press to it.
                        time.sleep(0.4)
                        control = str(event.get("control") or "")
                        button = WIZARD_BUTTONS.get(control)
                        if button is not None:
                            pad.tap(button, hold=0.12)
                        else:
                            client.send({"cmd": "skip_control"})
                gate, command = decide(gate)
                if command is not None:
                    sent.append(command)
                    client.send(command)
                time.sleep(0.02)

        try:
            step(1.5)
            assert {"cmd": "seating", "open": True, "players": 4, "hold": PAIR_HOLD} in sent
            pad.hold(BTN_SOUTH, PAIR)
            end = time.monotonic() + 30.0
            while time.monotonic() < end and not gate.done:
                step(0.3)
            story = "\n".join(seen[-30:])
            assert gate.done and gate.state == READY, f"never ready: state={gate.state} sent={sent}\n{story}"
            assert not any(c.get("cmd") in ("begin", "accept") for c in sent), f"a session was used: {sent}"
            assert not any(" accepted" in x or " assigning" in x for x in seen)
        finally:
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
            # The gate listens; one press seats the pad. Answer the wizard,
            # and the moment it closes press again and *do not let go*: the
            # hold that ends the wizard, still down when the door opens, is
            # the one that used to start the game.
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            pad.hold(BTN_SOUTH, PAIR)
            assert daemon.wait_for("claim", seconds=8.0) is not None, "a hold in front of the gate seated nobody"
            mapped = False
            end = time.monotonic() + 40.0
            while time.monotonic() < end and not mapped:
                for event in daemon.drain(0.2):
                    kind = event.get("event")
                    if kind == "mapping" and not event.get("done"):
                        time.sleep(0.4)
                        button = WIZARD_BUTTONS.get(str(event.get("control") or ""))
                        last = int(event.get("index") or 0) + 1 >= int(event.get("total") or 0)
                        if button is not None:
                            pad.tap(button, hold=0.12)
                        else:
                            daemon.send({"cmd": "skip_control"})
                        if last:
                            # Press *before* the wizard closes, and keep it
                            # down: this is the hold that used to ride
                            # through to the door and start the game.
                            time.sleep(0.2)
                            pad.down(0x136)
                    elif kind == "mapping" and event.get("done"):
                        mapped = True
            assert mapped, f"the wizard never finished: {daemon.states[-5:]}"

            # The button was down before the door opened and still is, well
            # past a second. It must not count.
            time.sleep(2.5)
            assert seat.poll() is None, "the gate started the game on the hold that was never let go"
            pad.up(0x136)
            time.sleep(0.5)
            assert seat.poll() is None

            # A fresh press, held past the hold, is the go.
            pad.hold(BTN_SOUTH, 1.3)
            end = time.monotonic() + 4.0
            while time.monotonic() < end and seat.poll() is None:
                time.sleep(0.1)
            assert seat.poll() == 0, "a hold past the full length did not start the game"
        finally:
            if seat.poll() is None:
                seat.send_signal(signal.SIGTERM)
                seat.wait(timeout=5)


def test_the_door_ignores_a_button_that_is_down_when_it_opens(daemon, sdl):
    """A real clone that already shows A pressed when the door opens -- as a
    Steam Controller's does, since padmap forwards its state -- must count
    for nothing until it comes up. Driven in-process, event by event."""
    from gotg_ui.seat import PAUSE, Door

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.run(1.0)                      # the clone is open in `sticks`
        sticks = picker.sticks

        # A press that began before the door existed, still down.
        pad.down(BTN_SOUTH)
        time.sleep(0.3)
        for _ in range(5):
            for event in sdl.event.get():
                if event.type == sdl.JOYDEVICEADDED:
                    sticks.add(event.device_index)
            time.sleep(0.05)
        assert sticks.any_button_down(), "the clone does not show the button down; nothing to prove"

        door = Door(sticks, time.monotonic(), seconds=1.0)
        end = time.monotonic() + 2.5
        while time.monotonic() < end:
            now = time.monotonic()
            for event in sdl.event.get():
                door.handle(event, now)
            door.tick(now)
            assert not door.done(now), "the door opened on a hold that was never let go"
            time.sleep(0.02)

        # Let go, and wait out the pause between pairing and readying up --
        # the same wait a person makes when the screen tells them to.
        pad.up(BTN_SOUTH)
        end = time.monotonic() + PAUSE + 0.5
        while time.monotonic() < end and not door.settled(time.monotonic()):
            now = time.monotonic()
            for event in sdl.event.get():
                door.handle(event, now)
            door.tick(now)
            time.sleep(0.02)
        assert door.settled(time.monotonic()), "the pause never ended"

        pad.down(BTN_SOUTH)
        opened = time.monotonic()
        while time.monotonic() < opened + 3.0 and not door.done(time.monotonic()):
            now = time.monotonic()
            for event in sdl.event.get():
                door.handle(event, now)
            door.tick(now)
            time.sleep(0.02)
        pad.up(BTN_SOUTH)
        took = time.monotonic() - opened
        assert door.done(time.monotonic()), "a fresh second-long hold did not open the door"
        assert 0.9 <= took <= 2.0, f"the door opened after {took:.2f}s, not a second"

        # And the door knows *who*. The go ring is drawn around that player's
        # own badge, so a second player holding a button has to be attributed
        # to the second player rather than to whoever sat down first.
        assert door.holder == 1, f"the hold was credited to {door.holder}, not to player one"
        assert 1 in door.heard(time.monotonic(), window=60.0), "the press was not attributed to a seat"
        picker.close()


def test_a_controller_switched_on_while_the_gate_is_up_takes_a_seat(daemon, sdl):
    """Note 1: the second player turned their pad on during the gate and could
    not join, because a session's pads are fixed when it opens. There is no
    session now; seating keeps looking."""
    import signal

    with FakePad("E2E Xbox Pad") as first:
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            first.hold(BTN_SOUTH, PAIR)
            claimed = daemon.wait_for("claim", seconds=8.0)
            assert claimed is not None and claimed.get("player") == 1

            # Only now does the second controller exist.
            with FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
                time.sleep(1.5)                 # padmap's scan finds it
                claimed = None
                for _ in range(3):
                    second.hold(BTN_SOUTH, PAIR)
                    claimed = daemon.wait_for("claim", seconds=4.0)
                    if claimed is not None:
                        break
                assert claimed is not None and claimed.get("player") == 2, (
                    "a controller switched on during the gate could not take a seat"
                )
                assert seat.poll() is None
        finally:
            seat.send_signal(signal.SIGTERM)
            seat.wait(timeout=5)


# --- change the bindings from the door ------------------------------------------


def test_a_hold_of_y_at_the_door_walks_the_buttons_again(daemon, sdl):
    """Note 2: from the door, somebody can ask for the wizard again. Y, held
    on the seated pad, and the daemon starts a fresh capture. Held rather than
    tapped: a thumb brushing Y reaching for A went back to the wizard, which
    reads exactly like the bindings not being remembered."""
    import signal

    with FakePad("E2E Xbox Pad") as pad:
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            pad.hold(BTN_SOUTH, PAIR)
            assert daemon.wait_for("claim", seconds=8.0) is not None
            steps = 0
            mapped = False
            end = time.monotonic() + 40.0
            while time.monotonic() < end and not mapped:
                for event in daemon.drain(0.2):
                    if event.get("event") == "mapping" and not event.get("done"):
                        steps += 1
                        time.sleep(0.4)
                        button = WIZARD_BUTTONS.get(str(event.get("control") or ""))
                        if button is not None:
                            pad.tap(button, hold=0.12)
                        else:
                            daemon.send({"cmd": "skip_control"})
                    elif event.get("event") == "mapping" and event.get("done"):
                        mapped = True
            assert mapped and steps >= 10, f"the first wizard did not run: {steps} steps"

            # The door is up. A tap of Y does nothing at all now, and a hold
            # of it asks for the walk again. Which raw button SDL calls Y
            # depends on whether padmap's mapping file existed when the gate
            # started (raw 2, the walk above) or SDL fell back to its Xbox
            # layout (raw 3); a person would press the one labelled Y. Both.
            time.sleep(1.5)
            assert seat.poll() is None
            for raw in (0x134, 0x133):
                pad.tap(raw, hold=0.15)
            assert daemon.wait_for("mapping", seconds=2.0) is None, "a tap of Y still walks the buttons"

            again = None
            for raw in (0x134, 0x133):
                pad.hold(raw, 1.4)
                again = daemon.wait_for("mapping", seconds=4.0)
                if again is not None:
                    break
            assert again is not None and not again.get("done"), "a hold of Y started no second walk"
            assert seat.poll() is None, "the gate exited instead of rebinding"
        finally:
            seat.send_signal(signal.SIGTERM)
            seat.wait(timeout=5)


# --- how fast a press reaches the game -----------------------------------------
#
# Melee felt like treacle: a jagged stick and buttons that answered late. It
# was not the port and not the pad -- padmap rescans every input device on
# every 20 ms tick while seating is open, one scan costs about 100 ms here (a
# udev walk plus a liveness probe of every hidraw node), and the forwarding
# waited behind it. These are the numbers, taken through a real clone, so it
# cannot come back quietly.

EVENT = struct.Struct("llHHi")          # struct input_event on 64-bit
EV_KEY_T, EV_ABS_T = 0x01, 0x03

# A frame at 60 Hz is 16.7 ms and a press should be nowhere near it. Generous
# against a busy machine; the number seen with seating closed is 0.03 ms.
A_FRAME_MS = 16.7


def _clone_node(player: int = 1) -> str | None:
    block = None
    with open("/proc/bus/input/devices") as devices:
        for line in devices:
            if line.startswith('N: Name="'):
                block = line.split('"')[1]
            elif line.startswith("H: Handlers=") and block == f"padmap Player {player}":
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        return f"/dev/input/{handler}"
    return None


def _drain(fd: int) -> list[tuple[int, int, int]]:
    import os

    out = []
    try:
        data = os.read(fd, EVENT.size * 256)
    except BlockingIOError:
        return out
    for at in range(0, len(data), EVENT.size):
        _, _, kind, code, value = EVENT.unpack_from(data, at)
        out.append((kind, code, value))
    return out


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _tap_latencies(pad: FakePad, fd: int, taps: int = 80) -> list[float]:
    """Milliseconds from writing a press to reading it on the clone."""
    import select

    out: list[float] = []
    _drain(fd)
    for turn in range(taps):
        value = 1 if turn % 2 == 0 else 0
        sent = time.monotonic()
        pad.down(BTN_SOUTH) if value else pad.up(BTN_SOUTH)
        end = sent + 0.6
        while time.monotonic() < end:
            if select.select([fd], [], [], 0.001)[0]:
                arrived = time.monotonic()
                if any(k == EV_KEY_T and c == BTN_SOUTH and v == value for k, c, v in _drain(fd)):
                    out.append((arrived - sent) * 1000)
                    break
        time.sleep(0.008)
    return out


def _seat_and_open_clone(daemon, sdl, pad: FakePad):
    """Seat the pad by a hold, then open the clone padmap published for it."""
    import os

    picker = Picker(_socket(daemon), sdl)
    picker.run(1.0)
    pad.hold(BTN_SOUTH, PAIR)
    assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
    node = None
    end = time.monotonic() + 8.0
    while time.monotonic() < end and node is None:
        node = _clone_node()
        time.sleep(0.2)
    assert node, "padmap seated the pad but published no clone"
    time.sleep(0.5)
    return picker, os.open(node, os.O_RDONLY | os.O_NONBLOCK)


def test_a_press_reaches_the_game_inside_a_frame(daemon, sdl):
    """With seating closed, as it is for the length of a game."""
    import os

    with FakePad("PERF Pad") as pad:
        picker, fd = _seat_and_open_clone(daemon, sdl, pad)
        try:
            # What gotg-seat sends as it hands over to the game.
            picker.padmap.send({"cmd": "seating", "open": False})
            time.sleep(1.0)
            latencies = _tap_latencies(pad, fd)
            assert len(latencies) > 60, f"only {len(latencies)} presses arrived at all"
            p95 = _percentile(latencies, 0.95)
            assert p95 < A_FRAME_MS, (
                f"a press took {p95:.1f} ms to reach the game (p50 "
                f"{_percentile(latencies, 0.5):.1f}, max {max(latencies):.1f}); "
                "a frame is 16.7 ms"
            )
        finally:
            os.close(fd)
            picker.close()


def test_the_stick_loses_nothing_on_the_way_through(daemon, sdl):
    """Every value the pad sends is a value the game sees, in order.

    A stick that arrives quantised is a stick that feels jagged, and a
    decompiled port reads the axis straight.
    """
    import os
    import select

    with FakePad("PERF Pad") as pad:
        picker, fd = _seat_and_open_clone(daemon, sdl, pad)
        try:
            picker.padmap.send({"cmd": "seating", "open": False})
            time.sleep(1.0)
            def drain_all() -> list[int]:
                """Everything waiting, not one bufferful: a reader that falls
                behind loses events to the kernel's own queue, and that is the
                reader's fault rather than padmap's."""
                got: list[int] = []
                while select.select([fd], [], [], 0)[0]:
                    batch = _drain(fd)
                    if not batch:
                        break
                    got += [v for k, c, v in batch if k == EV_ABS_T and c == ABS_X]
                return got

            drain_all()
            sent = [-30000 + step * 61 for step in range(400)]
            seen: list[int] = []
            for value in sent:
                pad.axis(ABS_X, value)
                time.sleep(0.005)                      # 200 Hz, a fast pad's rate
                seen += drain_all()
            time.sleep(0.4)
            seen += drain_all()

            assert seen, "the stick reached the game not at all"
            # From the first position that arrives, every one after it must.
            # The window before that is the clone still being published and
            # this reader still being the only one watching it.
            start = sent.index(seen[0])
            expected = sent[start:]
            assert seen == expected, (
                f"the stick arrived changed: {len(expected)} sent from the first seen, "
                f"{len(seen)} arrived, first difference at "
                f"{next((i for i, (a, b) in enumerate(zip(seen, expected, strict=False)) if a != b), len(seen))}"
            )
            assert len(seen) > len(sent) * 0.8, (
                f"only {len(seen)} of {len(sent)} stick positions arrived at all"
            )
        finally:
            os.close(fd)
            picker.close()


def test_a_pad_can_join_mid_game_without_costing_the_game_its_input(daemon, sdl):
    """The one GOTG gave up to get the latency back.

    Seating open is what lets a second player join in the middle of a level.
    It also costs about 100 ms a press, so the gate closes it before the game
    starts. When padmap's scan is cheap this passes, and the close in
    seat.py -- and this marker -- can go.
    """
    import os

    with FakePad("PERF Pad") as pad:
        picker, fd = _seat_and_open_clone(daemon, sdl, pad)
        try:
            picker.padmap.send({"cmd": "seating", "open": True, "players": 4, "hold": PAIR_HOLD})
            time.sleep(1.0)
            latencies = _tap_latencies(pad, fd)
            assert len(latencies) > 60, f"only {len(latencies)} presses arrived at all"
            p95 = _percentile(latencies, 0.95)
            assert p95 < A_FRAME_MS, (
                f"with seating open a press took {p95:.1f} ms (p50 "
                f"{_percentile(latencies, 0.5):.1f}); a frame is 16.7 ms"
            )
        finally:
            os.close(fd)
            picker.close()


# --- the door, with somebody else in the room ---------------------------------


def _walk_the_wizard(daemon, pad, seconds: float = 40.0) -> bool:
    """Answer every step with the fake pad's own buttons. True when it stored."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for event in daemon.drain(0.2):
            if event.get("event") == "mapping" and not event.get("done"):
                time.sleep(0.4)
                button = WIZARD_BUTTONS.get(str(event.get("control") or ""))
                if button is not None:
                    pad.tap(button, hold=0.12)
                else:
                    daemon.send({"cmd": "skip_control"})
            elif event.get("event") == "mapping" and event.get("done"):
                return True
    return False


def test_a_pad_that_has_been_mapped_is_never_asked_again(daemon, sdl):
    """The bindings are remembered, across a fresh daemon and a new launch.

    Reported as "after binding one controller it goes to the binding screen".
    padmap stores the capture and reports it back -- this is the gate keeping
    its side of that: seated, mapped, straight to the door, no `map` sent.
    """
    import signal

    with FakePad("E2E Xbox Pad") as pad:
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            pad.hold(BTN_SOUTH, PAIR)
            assert daemon.wait_for("claim", seconds=8.0) is not None
            assert _walk_the_wizard(daemon, pad), "the first walk never finished"
            time.sleep(1.5)
            assert seat.poll() is None, "the gate left before anybody was ready"
        finally:
            seat.send_signal(signal.SIGTERM)
            seat.wait(timeout=5)

        # The next launch: the same pad, the same console, nothing asked.
        daemon.seen.clear()
        again = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            # Seated, however many holds it takes: the gate unseats first, and
            # a hold that lands while the daemon is republishing reaches
            # nobody. A person holds again; so does this.
            seated = False
            for _ in range(4):
                pad.hold(BTN_SOUTH, PAIR)
                end = time.monotonic() + 4.0
                while time.monotonic() < end and not seated:
                    daemon.drain(0.2)
                    seated = bool(daemon.players)
                if seated:
                    break
            assert seated, "the pad took no seat on the second launch"
            # Long enough for a wizard to have started if it were going to.
            time.sleep(3.0)
            walked = [e for e in daemon.seen if e.get("event") == "mapping"]
            assert not walked, f"the gate asked to bind a pad it had already bound: {walked[:2]}"
            assert again.poll() is None
        finally:
            again.send_signal(signal.SIGTERM)
            again.wait(timeout=5)


def test_a_stray_press_at_the_door_neither_starts_the_game_nor_rebinds(daemon, sdl):
    """Only A or Start start it. Any button used to, and a thumb that brushed
    Y on the way went back to the wizard instead -- which reads exactly like
    bindings not being remembered."""
    import signal

    with FakePad("E2E Xbox Pad") as pad:
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            pad.hold(BTN_SOUTH, PAIR)
            assert daemon.wait_for("claim", seconds=8.0) is not None
            assert _walk_the_wizard(daemon, pad), "the walk never finished"
            time.sleep(1.5)

            # B, held well past the second. Not a starting button.
            daemon.seen.clear()
            pad.hold(0x131, 2.0)
            time.sleep(0.5)
            assert seat.poll() is None, "B started the game"
            walked = [e for e in daemon.seen if e.get("event") == "mapping"]
            assert not walked, "B went back to the wizard"

            # A, held past the gate's hold. That is the one.
            pad.hold(BTN_SOUTH, 1.3)
            end = time.monotonic() + 4.0
            while time.monotonic() < end and seat.poll() is None:
                time.sleep(0.1)
            assert seat.poll() == 0, "holding A did not start the game"
        finally:
            if seat.poll() is None:
                seat.send_signal(signal.SIGTERM)
                seat.wait(timeout=5)


def test_a_second_controller_joins_at_the_door_and_is_asked_for_its_buttons(daemon, sdl):
    """Reported as: no sign the second pad had control. The door never polled
    padmap, so a claim arrived and nothing on the screen knew."""
    import signal

    with FakePad("E2E Xbox Pad") as first:
        seat = _seat_process(daemon, {"PADMAP_SKIP_DAEMON_CHECK": "1"}, _root())
        try:
            assert daemon.wait_for("state", seconds=8.0) is not None
            time.sleep(1.0)
            first.hold(BTN_SOUTH, PAIR)
            assert daemon.wait_for("claim", seconds=8.0) is not None
            assert _walk_the_wizard(daemon, first), "the first walk never finished"
            time.sleep(1.5)                      # the door is up

            with FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
                time.sleep(1.5)                  # padmap's scan finds it
                # Patiently. With seating open the daemon spends its loop
                # rescanning every device -- one scan is ~100 ms against a
                # 20 ms tick -- and a hold can be missed outright, which is
                # the other half of "I could not pair my second controller".
                # See docs/requests/seating-costs-the-game-its-input.md; when
                # that scan is cheap, one hold will do.
                claimed = None
                for _ in range(5):
                    second.hold(BTN_SOUTH, PAIR)
                    claimed = daemon.wait_for("claim", seconds=4.0)
                    if claimed is not None:
                        break
                assert claimed is not None and claimed.get("player") == 2, (
                    "a controller held at the door took no seat"
                )
                # And the gate noticed: a pad with no idea what a GameCube is
                # gets asked, which only happens if the door polls padmap.
                step = daemon.wait_for("mapping", seconds=8.0)
                assert step is not None, "the door never noticed the second pad"
                assert seat.poll() is None
        finally:
            seat.send_signal(signal.SIGTERM)
            seat.wait(timeout=5)


# --- the dots beside the labels ---------------------------------------------
#
# What the door draws while somebody presses a button: a circle in that
# player's colour beside the label for the control they are pressing. It was
# drawn and it never appeared, because the two halves were speaking different
# languages -- padmap's profile answers `leftshoulder` and every label on an
# N64 drawing is called `L` -- and nothing in the suite compared the two. So
# this presses every control of a console on a real pad, through a real
# daemon's clone, and asks the door which label it would light.

# The fake pad declares BTN_SOUTH, EAST, NORTH, WEST, TL, TR, SELECT, START,
# which SDL numbers 0..7 in that order, and ABS_X/ABS_Y as axes 0 and 1. A
# padmap profile is written against those numbers, exactly as padmap's own
# capture would record them.
E2E_PROFILE_BUTTONS = {
    "a": {"kind": "button", "index": 0, "value": 0},
    "b": {"kind": "button", "index": 1, "value": 0},
    "x": {"kind": "button", "index": 2, "value": 0},
    "y": {"kind": "button", "index": 3, "value": 0},
    "leftshoulder": {"kind": "button", "index": 4, "value": 0},
    "rightshoulder": {"kind": "button", "index": 5, "value": 0},
    "back": {"kind": "button", "index": 6, "value": 0},
    "start": {"kind": "button", "index": 7, "value": 0},
    "leftstick_left": {"kind": "axis", "index": 0, "value": -1},
    "leftstick_right": {"kind": "axis", "index": 0, "value": 1},
    "leftstick_up": {"kind": "axis", "index": 1, "value": -1},
    "leftstick_down": {"kind": "axis", "index": 1, "value": 1},
}

# button code -> the label an N64 drawing puts it under, through
# src/client/data/ares-pads.json. `x` is the N64's B, which is the case worth
# having in here: a press is named by what the console calls it, not by what
# the pad calls it.
N64_CONTROLS = [
    (BTN_SOUTH, "A"),
    (0x133, "B"),          # BTN_NORTH -- the pad's X
    (0x136, "L"),          # BTN_TL
    (0x137, "R"),          # BTN_TR
    (BTN_START, "Start"),
]


def _profile(directory, name: str, buttons: dict | None = None) -> None:
    """A padmap device profile, written where the picker reads them."""
    import json
    import os

    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{name.replace(' ', '_')}.json"), "w") as out:
        # Both places padmap writes them: the top-level table, which is what
        # the door reads, and the universal scope beside it.
        json.dump(
            {
                "name": name,
                "buttons": buttons or E2E_PROFILE_BUTTONS,
                "mappings": {"": {"buttons": buttons or E2E_PROFILE_BUTTONS}},
            },
            out,
        )


def _door(sdl, sticks, tmp_path, monkeypatch, seats_named: dict[int, str]):
    """A door over these pads, reading profiles from tmp_path."""
    import pathlib

    from gotg_ui import profiles
    from gotg_ui.bindings import pad_controls
    from gotg_ui.seat import Door

    devices = str(tmp_path / "devices")
    for name in set(seats_named.values()):
        _profile(devices, name)
    monkeypatch.setenv("PADMAP_DEVICES", devices)
    # The ares table, from the checkout: `pad_controls` is the translation
    # under test and it is read off that file.
    monkeypatch.setenv("GOTG_DATA", str(pathlib.Path(__file__).parents[2] / "src" / "client" / "data"))
    names = pad_controls("Nintendo64")
    assert names.get("leftshoulder") == "L", "the ares table did not load; the rest proves nothing"

    by_seat = {
        player: (profiles.for_pad(name) or {}).get("buttons") or {}
        for player, name in seats_named.items()
    }
    assert all(by_seat.values()), f"no profile read for {seats_named}"
    door = Door(sticks, time.monotonic(), seconds=1.0, buttons_by=by_seat, names=names)
    return door


def _pump(sdl, door, seconds: float = 0.25) -> None:
    """Hand the door every event SDL has, for this long.

    `tick` as well as `handle`, because that is what the gate's own loop does
    and the door answers differently without it: the pause between pairing and
    readying up is measured against what the pads report, not against events.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        now = time.monotonic()
        for event in sdl.event.get():
            door.handle(event, now)
        door.tick(now)
        time.sleep(0.01)


def test_a_press_lights_the_label_for_that_control(daemon, sdl, tmp_path, monkeypatch):
    """Every control, one at a time: pressed it names its own label, released
    it names nothing. A dot that never appears is this returning {}."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(0.8)

        door = _door(sdl, picker.sticks, tmp_path, monkeypatch, {1: "E2E Xbox Pad"})
        _pump(sdl, door, 0.3)

        for code, label in N64_CONTROLS:
            pad.down(code)
            _pump(sdl, door, 0.3)
            assert door.lit_by == {1: {label}}, (
                f"pressing {hex(code)} lit {door.lit_by}, not {{1: {{{label!r}}}}}"
            )
            pad.up(code)
            _pump(sdl, door, 0.3)
            assert door.lit_by == {}, f"releasing {hex(code)} left {door.lit_by} lit"
        picker.close()


def test_the_stick_lights_the_axis_it_is_pushed_along(daemon, sdl, tmp_path, monkeypatch):
    """An axis, which is the half a button test would miss: it has no release
    of its own, only a return to the middle."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.run(0.8)

        door = _door(sdl, picker.sticks, tmp_path, monkeypatch, {1: "E2E Xbox Pad"})
        _pump(sdl, door, 0.3)

        pad.axis(ABS_X, -32767)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {1: {"X-Axis/Lo"}}, f"the stick pushed left lit {door.lit_by}"
        pad.axis(ABS_X, 32767)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {1: {"X-Axis/Hi"}}, f"the stick pushed right lit {door.lit_by}"
        pad.axis(ABS_X, 0)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {}, f"the stick back at rest left {door.lit_by} lit"
        picker.close()


def test_a_held_button_stays_lit_for_as_long_as_it_is_held(daemon, sdl, tmp_path, monkeypatch):
    """A hold is not a press that ends. The dot has to still be there a second
    later -- which is what somebody checking their controller actually does."""
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.run(0.8)

        door = _door(sdl, picker.sticks, tmp_path, monkeypatch, {1: "E2E Xbox Pad"})
        _pump(sdl, door, 0.3)

        pad.down(0x136)  # BTN_TL, the N64's L
        # The press has to arrive before it can stay: uinput to the daemon to
        # the clone to SDL is a few tens of milliseconds, and a test that
        # asserted immediately failed on the trip rather than on the hold.
        _pump(sdl, door, 0.3)
        assert door.lit_by == {1: {"L"}}, f"the hold never lit: {door.lit_by}"
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            _pump(sdl, door, 0.1)
            assert door.lit_by == {1: {"L"}}, f"mid-hold the label was {door.lit_by}"
        pad.up(0x136)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {}
        picker.close()


def test_two_players_pressing_at_once_light_their_own_labels(daemon, sdl, tmp_path, monkeypatch):
    """The whole point of the colour. Player two pressing L must not read as
    player one, and must not take player one's dot away."""
    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        assert picker.claim(first, 1), "the first pad took no seat"
        assert picker.claim(second, 2), "the second pad took no seat"
        picker.run(0.8)

        door = _door(
            sdl, picker.sticks, tmp_path, monkeypatch,
            {1: "E2E Xbox Pad", 2: "E2E Other Pad"},
        )
        _pump(sdl, door, 0.3)

        first.down(0x136)   # L
        second.down(BTN_SOUTH)  # A
        _pump(sdl, door, 0.4)
        assert door.lit_by == {1: {"L"}, 2: {"A"}}, f"two pads at once lit {door.lit_by}"

        first.up(0x136)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {2: {"A"}}, f"player one letting go left {door.lit_by}"
        second.up(BTN_SOUTH)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {}
        picker.close()


def test_the_door_rebinds_on_a_hold_of_y_and_not_a_tap(daemon, sdl):
    """A thumb brushing Y on the way to A went back to the wizard, which reads
    exactly like bindings not being remembered. It is a hold now."""
    from gotg_ui.seat import REBIND_HOLD, Door

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.run(0.8)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        _pump(sdl, door, 0.3)

        pad.tap(0x134, hold=0.08)  # BTN_WEST, SDL's Y
        _pump(sdl, door, 0.4)
        assert door.rebinding(time.monotonic()) == 0.0, "a tap of Y still asks to rebind"
        assert REBIND_HOLD == 3.0, f"the shipped rebind hold is {REBIND_HOLD}s, not three seconds"

        pad.down(0x134)
        end = time.monotonic() + REBIND_HOLD + 1.5
        while time.monotonic() < end and door.rebinding(time.monotonic()) < 1.0:
            _pump(sdl, door, 0.05)
        held = door.rebinding(time.monotonic())
        pad.up(0x134)
        assert held >= 1.0, f"holding Y for {REBIND_HOLD + 1.5:.1f}s did not ask to rebind"
        picker.close()


def test_the_go_is_the_shipped_three_second_hold(daemon, sdl):
    """How long the door actually waits, at the length that ships.

    A second was not enough: arriving at the door with a thumb still on A --
    which is how somebody gets there -- started the game before the screen
    had been read. Every hold this program times is three seconds now, and
    this one is measured through a real clone rather than read off the
    config, because the config is the half that was already right.
    """
    from gotg_ui.seat import GO_HOLD, Door

    assert GO_HOLD == 3.0, f"the shipped go hold is {GO_HOLD}s, not three seconds"

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(1.2)                     # the seating hold is long released

        door = Door(picker.sticks, time.monotonic())
        _pump(sdl, door, 1.2)               # past ARM_QUIET, nothing held
        pad.down(BTN_SOUTH)
        started = time.monotonic()
        _pump(sdl, door, 2.0)
        assert not door.done(time.monotonic()), (
            f"the door opened after {time.monotonic() - started:.1f}s, well short of three"
        )
        _pump(sdl, door, 1.6)
        took = time.monotonic() - started
        pad.up(BTN_SOUTH)
        assert door.done(time.monotonic()), f"three and a half seconds of holding was not the go ({took:.1f}s)"
        picker.close()


def test_a_press_lights_its_label_with_no_padmap_profile_at_all(daemon, sdl, tmp_path, monkeypatch):
    """Half the labels lit nothing, and this is why.

    padmap's capture holds the controls one console asked for: the Steam
    Controller's N64 capture has no `x` and its universal one has no
    `lefttrigger`, and the door was reading one table for both. A pad SDL maps
    says which control it is itself, in the layout the binding tables are
    written against -- so the dots no longer depend on what padmap happened to
    capture, and a pad with no profile whatsoever still lights its labels.
    """
    import pathlib

    from gotg_ui.bindings import pad_controls
    from gotg_ui.seat import Door

    monkeypatch.setenv("PADMAP_DEVICES", str(tmp_path / "empty"))
    monkeypatch.setenv("GOTG_DATA", str(pathlib.Path(__file__).parents[2] / "src" / "client" / "data"))

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(0.8)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0, names=pad_controls("Nintendo64"))
        assert not door.buttons and not door.buttons_by, "this test is meant to have no profile"
        _pump(sdl, door, 0.3)

        pad.down(BTN_SOUTH)
        _pump(sdl, door, 0.4)
        assert door.lit_by == {1: {"A"}}, f"with no profile, A lit {door.lit_by}"
        pad.up(BTN_SOUTH)
        _pump(sdl, door, 0.4)
        assert door.lit_by == {}, f"releasing A left {door.lit_by} lit"

        pad.down(0x136)  # BTN_TL -- SDL's left shoulder, the N64's L
        _pump(sdl, door, 0.4)
        assert door.lit_by == {1: {"L"}}, f"with no profile, L lit {door.lit_by}"
        pad.up(0x136)
        picker.close()


def test_a_hold_whose_release_was_missed_does_not_start_the_game(daemon, sdl):
    """The gate opening "basically instantly".

    A release can go missing: padmap republishes a clone and the button that
    was down on the old one never comes up on the new, and a Steam Controller
    forwards state rather than events. The hold kept its start time and
    finished three seconds later with nobody holding anything -- which lands
    as the game starting the moment A is touched. The pads are asked what is
    down now, so a hold nothing is holding does not count.
    """
    from gotg_ui.seat import Door

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.run(1.2)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        _pump(sdl, door, 1.2)               # armed: nothing held, past ARM_QUIET

        # The press reaches the door; the release is dropped on the floor, as
        # a republished clone drops it.
        pad.down(BTN_SOUTH)
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            for event in sdl.event.get():
                door.handle(event, time.monotonic())
            time.sleep(0.01)
        assert door.progress(time.monotonic()) > 0, "the press was not counted at all"
        pad.up(BTN_SOUTH)
        for event in sdl.event.get():
            if event.type in (sdl.CONTROLLERBUTTONUP, sdl.JOYBUTTONUP):
                continue                     # the missing release
            door.handle(event, time.monotonic())

        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            now = time.monotonic()
            for event in sdl.event.get():
                if event.type in (sdl.CONTROLLERBUTTONUP, sdl.JOYBUTTONUP):
                    continue
                door.handle(event, now)
            door.tick(now)
            assert not door.done(now), "a hold nobody is holding started the game"
            time.sleep(0.02)
        picker.close()


def test_the_hold_that_pairs_a_controller_cannot_also_start_the_game(daemon, sdl):
    """Pair, pause, ready -- and the pause is not optional.

    padmap claims a seat after a quarter second, and a thumb does not come off
    a button that fast. The same press went on into the go hold, so the game
    started while somebody was still reading the screen they had just reached.
    Nothing counts as a go until every pad has been quiet for the pause, and
    this holds one button from before the door exists until well past the go
    hold to prove it.
    """
    from gotg_ui.seat import PAUSE, Door

    assert PAUSE >= 1.0, f"the shipped pause is {PAUSE}s"

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)

        # The pairing hold, still down when the door opens -- which is what a
        # person does: hold A until the screen says they are in.
        pad.down(BTN_SOUTH)
        assert picker.until(lambda p: p.seated(1)), "holding a button seated nobody"
        picker.run(0.5)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        end = time.monotonic() + 1.0 + PAUSE + 1.5
        while time.monotonic() < end:
            now = time.monotonic()
            for event in sdl.event.get():
                door.handle(event, now)
            door.tick(now)
            assert not door.done(now), "the hold that paired the pad also started the game"
            # And while the clone still reports that button down, the pause
            # has not even begun. (A clone published *after* a press began
            # cannot report it: padmap forwards what happens next. A Steam
            # Controller does forward its state, which is the case
            # `test_the_door_ignores_a_button_that_is_down_when_it_opens`
            # covers with a real held button.)
            if picker.sticks.any_button_down():
                assert not door.settled(now), "the pause ended with a button still down"
            time.sleep(0.02)

        pad.up(BTN_SOUTH)
        end = time.monotonic() + PAUSE + 0.5
        while time.monotonic() < end and not door.settled(time.monotonic()):
            now = time.monotonic()
            for event in sdl.event.get():
                door.handle(event, now)
            door.tick(now)
            time.sleep(0.02)
        assert door.settled(time.monotonic()), f"the pause never ended after {PAUSE}s of quiet"

        # And now a fresh hold is the go.
        pad.down(BTN_SOUTH)
        end = time.monotonic() + 3.0
        while time.monotonic() < end and not door.done(time.monotonic()):
            now = time.monotonic()
            for event in sdl.event.get():
                door.handle(event, now)
            door.tick(now)
            time.sleep(0.02)
        pad.up(BTN_SOUTH)
        assert door.done(time.monotonic()), "a fresh hold after the pause did not start the game"
        picker.close()


def test_the_gate_holds_a_controllers_keyboard_so_it_cannot_start_the_game():
    """The leaking input, named.

    A Steam Controller in lizard mode types Enter when A is pressed, and the
    door takes Enter as "start now" -- so pairing one started the game with
    nobody having held anything. The picker has held those nodes since the day
    it was written; this screen never did.
    """
    from gotg_ui.hush import Hush, a_controllers, parse

    # A pad and the keyboard that belongs to it, sharing a phys the way an
    # Xbox pad's collections do on a USB port.
    with FakePad("E2E Xbox Pad", phys="e2e-hush/input0"), FakePad(
        "E2E Xbox Pad Keyboard", 0x045E, 0x028E, 2, phys="e2e-hush/input0", keyboard=True
    ):
        time.sleep(0.6)
        with open("/proc/bus/input/devices") as devices:
            text = devices.read()
        wanted = [node.name for node in a_controllers(parse(text))]
        assert any("E2E Xbox Pad Keyboard" in name for name in wanted), (
            f"the rule did not see the pad's keyboard as one: {wanted}"
        )

        hush = Hush()
        held = hush.refresh(text)
        try:
            node = event_node("E2E Xbox Pad Keyboard")
            assert node, "the fake keyboard has no event node to hold"
            assert node.rsplit("/", 1)[-1] in held, (
                f"the gate did not hold {node}; it could still type into the door"
            )
            # And the pad itself is untouched: grabbing that would take the
            # presses away from padmap, which is the thing reading them.
            joystick = event_node("E2E Xbox Pad")
            assert joystick and joystick.rsplit("/", 1)[-1] not in held
        finally:
            hush.release()


def test_one_input_lights_one_label_even_when_the_profile_disagrees(daemon, sdl, tmp_path, monkeypatch):
    """A GameCube pad's right trigger lit R *and* Z at once.

    Two vocabularies name a press -- SDL's standard layout for a pad it maps,
    padmap's capture for one it does not -- and the choice was being made per
    *event* rather than per pad: a button took the first, an axis took both.
    A profile binding something else to the same axis then lit a second label
    that nobody had pressed.

    So the profile here disagrees on purpose. It says axis 0 is
    `lefttrigger`, which an N64 drawing calls Z; SDL's standard layout calls
    axis 0 the left stick's X, which that drawing calls X-Axis. The pad is one
    SDL maps, so SDL names it -- and either way exactly one label lights.
    """
    import pathlib

    from gotg_ui import profiles
    from gotg_ui.bindings import pad_controls
    from gotg_ui.seat import Door

    devices = str(tmp_path / "devices")
    _profile(devices, "E2E Xbox Pad", {
        **E2E_PROFILE_BUTTONS,
        "lefttrigger": {"kind": "axis", "index": 0, "value": -1},
    })
    monkeypatch.setenv("PADMAP_DEVICES", devices)
    monkeypatch.setenv("GOTG_DATA", str(pathlib.Path(__file__).parents[2] / "src" / "client" / "data"))

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(0.8)

        door = Door(
            picker.sticks, time.monotonic(), seconds=1.0,
            buttons_by={1: profiles.bindings(profiles.for_pad("E2E Xbox Pad"), "console:n64")},
            names=pad_controls("Nintendo64"),
        )
        assert door.buttons_by[1].get("lefttrigger"), "the disagreeing profile was not read"
        _pump(sdl, door, 0.3)

        pad.axis(ABS_X, -32767)
        _pump(sdl, door, 0.4)
        assert door.lit_by == {1: {"X-Axis/Lo"}}, (
            f"one axis lit {door.lit_by}; two labels for one input is the bug"
        )
        pad.axis(ABS_X, 0)
        _pump(sdl, door, 0.3)
        assert door.lit_by == {}, f"the stick back at rest left {door.lit_by} lit"

        # A button too, for the pad that was reported: one press, one label.
        pad.down(0x136)  # BTN_TL, the N64's L
        _pump(sdl, door, 0.4)
        assert door.lit_by == {1: {"L"}}, f"BTN_TL lit {door.lit_by}"
        pad.up(0x136)
        picker.close()


def test_a_seat_takes_the_hold_the_picker_asked_for(daemon, sdl):
    """A quarter second was padmap's, and it was too quick.

    Picking a controller up, or resting a thumb on one while reading the
    screen, claimed a seat nobody meant to claim. padmap now takes the length
    on the `seating` command (98fd757); this is that asked for and measured
    through the daemon, because a field a daemon ignores looks exactly like a
    field that works.
    """
    from gotg_ui.gate import PAIR_HOLD

    assert PAIR_HOLD >= 1.0, f"this test is about a deliberate hold, not {PAIR_HOLD}s"

    with FakePad("E2E Xbox Pad") as pad:
        daemon.send({"cmd": "seating", "open": True, "players": 4, "hold": PAIR_HOLD})
        daemon.drain(1.0)

        # Well past padmap's own default, and well short of what was asked for.
        pad.hold(BTN_SOUTH, 0.6)
        assert daemon.wait_for("claim", seconds=1.5) is None, (
            "a hold shorter than the one asked for still took a seat"
        )

        # And the length actually asked for does claim one. Twice, because a
        # hold can be missed outright while seating rescans every device --
        # docs/requests/seating-costs-the-game-its-input.md.
        claimed = None
        for _ in range(3):
            pad.hold(BTN_SOUTH, PAIR_HOLD + 1.0)
            claimed = daemon.wait_for("claim", seconds=3.0)
            if claimed is not None:
                break
        assert claimed is not None, f"a hold of {PAIR_HOLD + 1.0}s took no seat"
        assert claimed.get("player") == 1


def test_a_stick_reads_as_a_position_not_as_four_labels(daemon, sdl):
    """What the ring on the drawing draws.

    Four labels say what a stick is made of and none of them says where it is,
    so the directions became one ring with a dot in it. The dot is this:
    player -> stick -> (x, y), taken from SDL's own axis order through a real
    clone, with a deadzone so a pad that rests a percent off centre does not
    draw a dot that never sits still.
    """
    from gotg_ui.seat import STICK_DEAD, Door

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(0.8)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        _pump(sdl, door, 0.3)
        assert door.sticks_by == {}, "a stick nobody has touched is not a reading"

        pad.axis(ABS_X, -32767)
        _pump(sdl, door, 0.4)
        where = door.sticks_by.get(1, {}).get("left")
        assert where is not None, f"the stick moved and nothing read it: {door.sticks_by}"
        assert where[0] < -0.8, f"pushed hard left, the dot sat at {where}"
        assert abs(where[1]) <= STICK_DEAD, f"the other axis moved on its own: {where}"

        pad.axis(ABS_X, 32767)
        _pump(sdl, door, 0.4)
        assert door.sticks_by[1]["left"][0] > 0.8, f"pushed hard right: {door.sticks_by}"

        # Back to the middle, and it reads as the middle rather than as a few
        # percent of drift.
        pad.axis(ABS_X, 0)
        _pump(sdl, door, 0.4)
        assert abs(door.sticks_by[1]["left"][0]) <= STICK_DEAD, (
            f"at rest the dot sat at {door.sticks_by[1]['left']}"
        )
        picker.close()


def test_the_keyboard_drives_nothing_until_padmap_seats_it(daemon, sdl):
    """The keyboard goes through padmap now, like everything else.

    Enter and Esc at the door mean "start the game"; an unseated keyboard is
    any device in the room that types, and most controllers are one. So the
    door hears no key at all until padmap reports a keyboard seat -- and the
    space bar, which is how the keyboard asks for one, is heard always.
    """
    from gotg_ui import keys as keys_rule
    from gotg_ui.seat import Door

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(1.2)

        assert not keys_rule.drives(picker.players, picker.padmap.connected), (
            "a pad seat is not a keyboard seat"
        )

        door = Door(picker.sticks, time.monotonic(), seconds=1.0, keys_drive=False)
        _pump(sdl, door, 1.2)
        enter = sdl.event.Event(sdl.KEYDOWN, key=sdl.K_RETURN, mod=0, unicode="\\r", scancode=40)
        assert not door.handle(enter, time.monotonic()), "an unseated keyboard started the game"
        escape = sdl.event.Event(sdl.KEYDOWN, key=sdl.K_ESCAPE, mod=0, unicode="", scancode=41)
        assert not door.handle(escape, time.monotonic()), "an unseated keyboard left the gate"

        # padmap seats it, the rule turns over, and the same key is the way out.
        picker.padmap.seat_keyboard()
        assert picker.until(
            lambda p: keys_rule.drives(p.players, p.padmap.connected), seconds=6.0
        ), f"padmap seated no keyboard: {picker.players}"

        door.keys_drive = True
        assert door.handle(enter, time.monotonic()), "a seated keyboard could not start the game"
        picker.close()


def test_a_smooth_shape_is_made_once_and_kept(sdl):
    """The rings and checks are supersampled -- painted at four times the
    size on a transparent surface and scaled down, because pygame's thick
    lines and polygons have hard pixel edges and a green ring made of
    staircases is the first thing anybody notices on a still screen.

    That is only affordable because every one of them is the same shape again
    next frame: a ring at the same fraction, a check at the same size. Here
    with pygame, because the dev venv has none.
    """
    from gotg_ui import controllers

    controllers._shapes.clear()
    painted = []

    def paint(surface, scale):
        painted.append(scale)

    first = controllers.crisp(("e2e", 1), (10, 10), paint)
    again = controllers.crisp(("e2e", 1), (10, 10), paint)
    assert first is again, "the same shape was made twice"
    assert painted == [controllers.CRISP], "painted once, at the supersampled size"

    controllers.crisp(("e2e", 2), (10, 10), paint)
    assert len(painted) == 2, "a different key is a different shape"

    # And it cannot grow without end: a fraction off a clock is a new key
    # about ninety times a hold.
    for step in range(controllers._SHAPES_KEPT + 5):
        controllers.crisp(("e2e-fill", step), (4, 4), lambda surface, scale: None)
    assert len(controllers._shapes) <= controllers._SHAPES_KEPT
    controllers._shapes.clear()


def test_a_ring_at_the_same_fraction_is_not_redrawn_every_frame(sdl):
    """A fraction is a float off a clock, so two frames a thousandth apart
    would be two shapes. Quantising is what makes the cache work at all."""
    from gotg_ui import controllers

    controllers._shapes.clear()
    screen = sdl.display.set_mode((200, 200))
    for step in range(30):
        # A hold ticking along at sixty frames a second, a millisecond apart.
        controllers.draw_arc(screen, (100, 100), 30, (100, 200, 100), 0.5 + step * 0.0001, 4)
    assert len(controllers._shapes) == 1, f"one ring became {len(controllers._shapes)} surfaces"
    controllers._shapes.clear()


# --- two people pairing at once -----------------------------------------------
#
# One person pairing is a fraction. Two is a queue: both fills have to be on
# screen, and the seats have to go out in the order the buttons went down --
# not in the order the pads were plugged in, which is what the daemon used to
# do and what nobody on a sofa can see. padmap names the pad on every
# `progress` now (`frac`, `name`, `node`, `player`) and says `frac: 0` when
# one lets go; these are that, end to end, with two real pads.


def _progress_by_node(daemon, seconds: float) -> dict[str, list[float]]:
    """Every `progress` reading of the last `seconds`, by the pad it names."""
    found: dict[str, list[float]] = {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for event in daemon.drain(0.1):
            if event.get("event") != "progress":
                continue
            found.setdefault(str(event.get("node") or ""), []).append(float(event.get("frac", 0)))
    return found


def test_two_pads_holding_at_once_are_two_fills(daemon, sdl):
    """Both people see themselves, and the readings say which is which.

    Anonymous readings -- one `frac` per pad per tick with no pad on it --
    arrive as a single fill jumping between two values, which is what this
    looked like before padmap named them.
    """
    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        daemon.send({"cmd": "seating", "open": True, "players": 4, "hold": 1.5})
        daemon.drain(0.5)

        first.down(BTN_SOUTH)
        time.sleep(0.2)
        second.down(BTN_SOUTH)
        readings = _progress_by_node(daemon, 1.0)
        first.up(BTN_SOUTH)
        second.up(BTN_SOUTH)

        named = {node: fracs for node, fracs in readings.items() if node}
        assert len(named) == 2, f"two pads holding gave {len(named)} fills: {readings}"
        for node, fracs in named.items():
            assert max(fracs) > 0, f"{node} filled nothing"
            assert fracs == sorted(fracs), f"{node}'s fill went backwards: {fracs}"


@pytest.mark.xfail(
    strict=True,
    reason="a claim clears every other hold in flight at this pin; "
           "see docs/requests/two-people-pairing-at-once.md",
)
def test_the_seat_goes_to_whoever_pressed_first(daemon, sdl):
    """Not to whichever pad was plugged in first, which is what the daemon
    used to do when two holds finished in the same tick.

    Press order is right at this pin; the second claim never arrives, because
    the first claim's `seating.reset()` clears the other pad's hold and a
    button that is already down sends no new edge to restart it.
    """
    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        daemon.send({"cmd": "seating", "open": True, "players": 4, "hold": 1.0})
        daemon.drain(0.5)
        daemon.seen.clear()

        # The second pad first, by a clear margin, so "press order" and "pad
        # order" disagree and the answer says which one won.
        second.down(BTN_SOUTH)
        time.sleep(0.35)
        first.down(BTN_SOUTH)
        claims = []
        end = time.monotonic() + 6.0
        while time.monotonic() < end and len(claims) < 2:
            for event in daemon.drain(0.2):
                if event.get("event") == "claim":
                    claims.append(event)
        second.up(BTN_SOUTH)
        first.up(BTN_SOUTH)

        assert len(claims) == 2, f"two holds, {len(claims)} claims: {claims}"
        assert claims[0]["name"] == "E2E Other Pad", f"the earlier press did not go first: {claims}"
        assert claims[0]["player"] == 1 and claims[1]["player"] == 2


def test_letting_go_loses_the_place_and_says_so(daemon, sdl):
    """A release is a reading of its own -- `frac: 0` for that pad -- because
    silence cannot say *which* of two pads stopped. And the seat that hold was
    filling towards goes to whoever does finish."""
    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        daemon.send({"cmd": "seating", "open": True, "players": 4, "hold": 1.5})
        daemon.drain(0.5)
        daemon.seen.clear()

        first.down(BTN_SOUTH)
        time.sleep(0.2)
        second.down(BTN_SOUTH)
        time.sleep(0.5)
        first.up(BTN_SOUTH)          # gives up its place

        released = None
        end = time.monotonic() + 2.0
        while time.monotonic() < end and released is None:
            for event in daemon.drain(0.2):
                if event.get("event") == "progress" and float(event.get("frac", 1)) == 0.0:
                    released = event
        assert released is not None, "letting go said nothing at all"
        assert "E2E Xbox Pad" in str(released.get("name")), f"the wrong pad was said: {released}"

        claimed = daemon.wait_for("claim", seconds=6.0)
        second.up(BTN_SOUTH)
        assert claimed is not None, "the pad that kept holding never took a seat"
        assert claimed["name"] == "E2E Other Pad"
        assert claimed["player"] == 1, "the seat the other pad gave up was not the one taken"


def test_two_pads_can_both_ready_up_without_locking_each_other_out(daemon, sdl):
    """The deadlock, on real pads.

    The door had one hold for the whole room and one pause measured across
    every pad: while one player held A the room was never quiet, so nobody
    else's press counted -- and when the first let go the second was still
    holding, so it still never counted. Neither could ready up.
    """
    from gotg_ui.seat import Door

    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        assert picker.claim(first, 1), "the first pad took no seat"
        assert picker.claim(second, 2), "the second pad took no seat"
        picker.run(1.0)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        door.seats = {1, 2}
        _pump(sdl, door, 1.4)                   # both pads quiet, past the pause

        # Both thumbs down, together, which is the case that locked.
        first.down(BTN_SOUTH)
        second.down(BTN_SOUTH)
        end = time.monotonic() + 4.0
        while time.monotonic() < end and not door.everybody(time.monotonic()):
            _pump(sdl, door, 0.05)
        first.up(BTN_SOUTH)
        second.up(BTN_SOUTH)

        assert door.holds.ready == {1, 2}, (
            f"two pads held A together and {sorted(door.holds.ready)} readied up"
        )


def test_the_game_waits_for_every_seated_pad(daemon, sdl):
    """One player ready is not the room ready."""
    from gotg_ui.seat import Door

    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        assert picker.claim(first, 1)
        assert picker.claim(second, 2)
        picker.run(1.0)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        door.seats = {1, 2}
        _pump(sdl, door, 1.4)

        first.down(BTN_SOUTH)
        end = time.monotonic() + 3.0
        while time.monotonic() < end and 1 not in door.holds.ready:
            _pump(sdl, door, 0.05)
        first.up(BTN_SOUTH)
        assert 1 in door.holds.ready, "the first pad never readied up"
        _pump(sdl, door, 0.5)
        assert not door.everybody(time.monotonic()), "one player ready started the game"
        assert door.waiting_on() == {2}

        # And the second finishes the room.
        _pump(sdl, door, 1.2)
        second.down(BTN_SOUTH)
        end = time.monotonic() + 3.0
        while time.monotonic() < end and not door.everybody(time.monotonic()):
            _pump(sdl, door, 0.05)
        second.up(BTN_SOUTH)
        assert door.everybody(time.monotonic()), f"both held and the room is {sorted(door.holds.ready)}"


def test_a_player_who_readied_up_keeps_it_while_the_others_catch_up(daemon, sdl):
    """Nobody should have to keep holding while somebody else finds their pad."""
    from gotg_ui.seat import Door

    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        assert picker.claim(pad, 1)
        picker.run(1.0)

        door = Door(picker.sticks, time.monotonic(), seconds=1.0)
        door.seats = {1, 2}                      # player two is still pairing
        _pump(sdl, door, 1.4)

        pad.down(BTN_SOUTH)
        end = time.monotonic() + 3.0
        while time.monotonic() < end and 1 not in door.holds.ready:
            _pump(sdl, door, 0.05)
        pad.up(BTN_SOUTH)
        assert 1 in door.holds.ready

        _pump(sdl, door, 2.0)                    # a long time doing nothing
        assert 1 in door.holds.ready, "a player lost their readiness by letting go"
        assert door.waiting_on() == {2}
        picker.close()
