"""The controller requirement, against a real daemon and real devices.

A controller danstick has published is the one thing that may drive this
picker (the keyboard is one too, once danstick has seated it -- keys.py).
Nothing else -- and a pad becomes one of danstick's by being picked up and
held, from wherever the picker happens to be, never from a screen somebody
had to find first.

The picker has no controller screens any more. Keeping danstick listening for
a hold is the overlay's (`gotg-killswitch`, src/seating.rs), which runs beside
the picker and over every game; the picker only reads what danstick says and
refuses every pad danstick has not published. So these tests run the picker's
own modules -- `gotg_ui.danstick`, `gotg_ui.pads`, `gotg_ui.hush`,
`gotg_ui.keys` -- beside a stand-in for the overlay's `seating` rule, against
a danstick daemon of its own and controllers made out of /dev/uinput. Nothing
is mocked, deliberately: every bug this suite was written after lived in the
gap between what the code believed about SDL and what SDL does. A mock would
have agreed with the code and shipped the bug.

Run them on the cluster (`nix run .#controllers-cluster`), or with
`nix run .#test-controllers`, which brings danstick and a pygame.
GOTG_E2E_REQUIRE=1 turns "cannot run here" into a failure.
"""

from __future__ import annotations

import struct
import time

from fakepad import ABS_X, BTN_SOUTH, BTN_START, FakePad, event_node, kernel_names

from gotg_ui import config, pads
from gotg_ui import keys as keys_rule
from gotg_ui.danstick import Danstick

# Long enough for a daemon scan (1s), a hold (0.25s) and a republish, with the
# slack a loaded machine needs. Tests wait for a condition, not for this.
PATIENCE = 12.0

# How long a hold takes to claim a seat: the theme's length, which the picker
# exports to the daemon as DANSTICK_HOLD_SECONDS and the overlay sends on every
# `seating`. Read from the same place rather than written down here, because
# it used to be a flat 0.7 s -- comfortably past danstick's own quarter second
# -- and then pairing started asking for a second and a half, and every test
# that seated a pad stopped seating one.
PAIR_HOLD = float(config.get("theme.timeouts.pair_hold", 1.5))

# Long enough to claim a seat, with a little over for the trip to the daemon.
PAIR = PAIR_HOLD + 0.6

# Seats the overlay asks for before danstick has said how many it has
# (seating.rs's SLOTS).
SLOTS = 4


class Listening:
    """The overlay's half of pairing, line for line with seating.rs.

    The picker used to keep danstick's `seating` open (assign.Watch), and the
    launch gate before a game; both are gone, and `gotg-killswitch` asks
    instead. The overlay is a Rust program the test image does not carry, so
    its rule is here: ask on each connection, and again only when a `state`
    says danstick stopped listening while a seat is free and no session is
    open. Asking more often than that is not harmless -- a second `seating`
    with a different hold drops every hold in flight.
    """

    def __init__(self, hold: float = PAIR_HOLD):
        self.hold = hold
        self.slots = SLOTS
        self.asked = False
        self.refused = False

    def lost(self) -> None:
        self.asked = False

    def apply(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "state":
            slots = event.get("slots")
            if isinstance(slots, int) and slots > 0:
                self.slots = slots
            seated = [p for p in event.get("players") or [] if isinstance(p, dict)]
            free = len(seated) < self.slots
            if event.get("seating") is False and free and event.get("state") != "assigning":
                self.asked = False
        elif kind == "error" and event.get("message") == 'unknown command "seating"':
            self.refused = True

    def wanted(self) -> dict | None:
        if self.refused or self.asked:
            return None
        self.asked = True
        return {"cmd": "seating", "open": True, "players": self.slots, "hold": self.hold}


class Picker:
    """The picker's controller half, one frame at a time, with the overlay beside it.

    What the event loop does: read danstick, then read the pads -- plus what
    the overlay does on the same socket, keep `seating` open. What is not here
    is the grid, the art and the window -- none of which has an opinion about
    who may press a button.
    """

    def __init__(self, socket_path, sdl):
        self.sdl = sdl
        self.danstick = Danstick(socket_path)
        assert self.danstick.connect(), "the picker could not reach the test daemon"
        self.listening = Listening()
        self.sticks = pads.init()
        self.sent: list[dict] = []
        self.pressed: list[str] = []
        self.progress_seen = 0.0

    def frame(self) -> None:
        for event in self.danstick.poll():
            self.listening.apply(event)
            if event.get("event") == "progress":
                try:
                    self.progress_seen = max(self.progress_seen, float(event.get("frac") or 0.0))
                except (TypeError, ValueError):
                    pass
        listen = self.listening.wanted()
        if listen is not None:
            self.sent.append(listen)
            self.danstick.send(listen)

        for event in self.sdl.event.get():
            # Exactly what app.py does with a pad arriving or leaving: danstick
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
        return self.danstick.players

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
            # that slept through it left nothing to draw it from and the test
            # with nothing to prove.
            pad.down(BTN_SOUTH)
            self.run(PAIR)
            pad.up(BTN_SOUTH)
            if self.until(lambda p: p.seated(player), seconds=4.0):
                return True
        return False

    def close(self) -> None:
        self.danstick.close()


# --- the requirement --------------------------------------------------------


def test_the_picker_opens_with_no_controllers(daemon, sdl):
    """Nothing is seated until somebody claims a seat, however much is plugged in."""
    with FakePad("E2E Xbox Pad"):
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)

        assert picker.players == [], "a pad was seated without anybody holding anything"
        assert picker.danstick.status_word != "assigning", "a session opened with nobody asking"
        assert not keys_rule.drives(picker.players, picker.danstick.connected), (
            "the keyboard drives the picker with nobody seated"
        )
        picker.close()


def test_a_pad_danstick_has_not_published_cannot_move_the_picker(daemon, sdl):
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
        # Asked, with the theme's hold. Perhaps twice: the `state` answering
        # the client's own `status` can arrive after the first ask and still
        # say "not listening", which is the overlay's cue to ask again -- the
        # same hold, so no hold in flight is dropped.
        asked = {"cmd": "seating", "open": True, "players": SLOTS, "hold": PAIR_HOLD}
        assert picker.sent and all(command == asked for command in picker.sent), (
            f"danstick was not asked to listen for a {PAIR_HOLD}s hold: {picker.sent}"
        )

        # Patiently: while seating is open the daemon spends its loop
        # rescanning, and a hold can reach nobody. See
        # docs/requests/seating-costs-the-game-its-input.md.
        assert picker.claim(pad, 1), "holding a button seated nobody"

        assert picker.progress_seen > 0, "nothing filled the ring while the button was held"
        assert "danstick Player 1" in kernel_names(), "danstick seated a player but published no pad"
        assert [p["player"] for p in picker.players] == [1], f"one hold, seats {picker.players}"
        picker.close()


def test_and_that_controller_then_drives_the_picker(daemon, sdl):
    """The other half: once danstick has published it, its presses are the picker's."""
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
        assert nodes[1] != nodes[2], "two seats, one controller -- danstick seated the same pad twice"
        assert {"danstick Player 1", "danstick Player 2"} <= set(kernel_names())
        picker.close()


def test_a_hold_under_seating_never_opens_a_session(daemon, sdl):
    """A session grabs every pad and takes the screen. That is the detour, and it is gone.

    Nothing beside the picker asks for one -- the overlay sends `seating` and
    nothing else -- and a hold under `seating` claims a seat without danstick
    opening one of its own (DANSTICK_NO_AUTOSETUP keeps it from walking an
    unmapped pad here; the overlay's `map` is a pad's, not a session).
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        picker.until(lambda p: p.seated(1))
        picker.run(0.5)

        assert picker.seated(1), "holding a button seated nobody"
        assert all(command["cmd"] == "seating" for command in picker.sent), (
            f"something other than seating was sent: {picker.sent}"
        )
        daemon.drain(0.5)
        assert "assigning" not in daemon.states, (
            "a session was opened -- the pairing screen is back"
        )
        picker.close()


def test_with_no_danstick_at_all_a_pad_still_cannot_move_the_picker(sdl):
    """The bug as it was seen: the picker opened, no daemon anywhere, and an
    Xbox pad that danstick had never heard of moved the cursor.

    No `daemon` fixture on purpose. Nothing is running, nothing is on the
    socket, and the pad is refused anyway -- because the rule is about the
    pad, not about danstick's state.
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
                    assert pads.button(event) is None, "a pad danstick never published drove the picker"
                elif event.type == sdl.JOYHATMOTION:
                    assert pads.direction(event) is None
        assert saw, "SDL never delivered the press, so nothing was proven"


def test_the_pad_rule_never_swallows_a_key(sdl):
    """The keyboard has a rule of its own (keys.py: heard once danstick seats
    it), and this one is not it.

    The pad filter is only ever asked about pads: a key is not a pad event and
    cannot be swallowed by it, whatever danstick is doing.
    """
    key = sdl.event.Event(sdl.KEYDOWN, {"key": sdl.K_a, "unicode": "a", "mod": 0})
    assert pads.button(key) is None
    assert pads.direction(key) is None


def _socket(daemon):
    """Where this test's daemon listens, as the picker's client wants it."""
    from pathlib import Path

    return Path(daemon.path)



def test_a_controller_can_still_join_after_the_picker_has_left_for_a_game(daemon, sdl):
    """Any time: the grid, a game, anything. The picker is gone and a hold still seats you.

    Over a game the overlay is on the socket, asking again whenever danstick
    stops listening; this is the floor under that. The client that asked
    danstick to listen disconnects, the observer disconnects, and a pad is
    held with nobody on the socket at all: seating stays open by itself.
    """
    from conftest import Daemon

    with FakePad("E2E Xbox Pad") as first, FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        first.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))
        picker.close()          # the picker execs into the game
        daemon.close()          # and nothing else is on the socket
        time.sleep(0.5)

        second.hold(BTN_SOUTH, PAIR)
        time.sleep(2.0)

        later = Daemon(daemon.path)
        try:
            numbered = sorted(p["player"] for p in later.players)
        finally:
            later.close()
        assert numbered == [1, 2], f"a pad held mid-game took no seat: {numbered}"
        assert "danstick Player 2" in kernel_names()


def test_a_controller_switched_on_after_seating_opened_takes_a_seat(daemon, sdl):
    """The second player turned their pad on during the launch gate and could
    not join, because a session's pads are fixed when it opens. There is no
    session and no gate now; `seating` is opened once, when the overlay
    connects, and has to keep finding pads that did not exist then -- a pad
    switched on mid-level is the same case."""
    with FakePad("E2E Xbox Pad") as first:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        assert picker.claim(first, 1), "the first pad took no seat"

        # Only now does the second controller exist.
        with FakePad("E2E Other Pad", 0x2AAA, 0x5BBB, 1) as second:
            picker.run(1.5)                 # danstick's scan finds it
            assert picker.claim(second, 2), "a controller switched on after seating opened took no seat"
        picker.close()


# --- every session starts unseated ------------------------------------------


def test_a_new_session_opens_with_nobody_seated_whatever_danstick_remembers(daemon, sdl, monkeypatch):
    """Open the picker: nobody is seated until somebody holds a button.

    The daemon the fixture started remembers a seat. The picker's own
    `ensure_daemon` -- fresh, following this pid -- replaces it with one that
    does not, which is what danstick built for docs/requests/session-daemon.md.
    """
    import os
    import signal

    from gotg_ui.danstick import ensure_daemon

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
        monkeypatch.setenv("DANSTICK_SKIP_DAEMON_CHECK", "0")
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
            assert picker.danstick.state.get("following") == os.getpid(), (
                "the daemon is not following the picker"
            )
            # And the seat is still there for the taking: the same pad, held
            # again, is player one again.
            pad.hold(BTN_SOUTH, PAIR)
            assert picker.until(lambda p: p.seated(1)), "the fresh daemon seated nobody"
        finally:
            fresh_pid = picker.danstick.state.get("pid")
            picker.close()
            if isinstance(fresh_pid, int):
                try:
                    os.kill(fresh_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


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


# --- the keyboard as a player -------------------------------------------------


def test_the_keyboard_takes_a_seat_when_asked(daemon, sdl):
    """Space held on the grid ends in this command, and danstick answers with a
    seat named Keyboard, icon keyboard, in the next free slot.

    The hold itself is timed by the picker and tested in tests/ui; this is
    the daemon's half. It was a strict xfail against
    docs/requests/keyboard-as-a-player.md until danstick answered it, which is
    how the marker came off: the suite failed for passing.
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1))

        picker.danstick.seat_keyboard()
        seated = picker.until(
            # "Keyboard and Mouse", icon keyboard-mouse since danstick afe321d;
            # `keyboard` is the flag that does not change with the name.
            lambda p: any(pl.get("player") == 2 and pl.get("keyboard") for pl in p.players),
            seconds=4.0,
        )
        assert seated, f"danstick seated no keyboard: {picker.players}"
        picker.close()


# --- how fast a press reaches the game -----------------------------------------
#
# Melee felt like treacle: a jagged stick and buttons that answered late. It
# was not the port and not the pad -- danstick rescans every input device on
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
            elif line.startswith("H: Handlers=") and block == f"danstick Player {player}":
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
    """Seat the pad by a hold, then open the clone danstick published for it."""
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
    assert node, "danstick seated the pad but published no clone"
    time.sleep(0.5)
    return picker, os.open(node, os.O_RDONLY | os.O_NONBLOCK)


def test_a_press_reaches_the_game_inside_a_frame(daemon, sdl):
    """With seating closed: the floor under the next two.

    Nothing in a session closes seating any more -- gotg-seat used to as it
    handed over to the game, and the overlay now keeps it open over every
    game (the mid-game test below). A press slow with nothing else going on is
    danstick's forwarding itself rather than its scan, and this says which.
    """
    import os

    with FakePad("PERF Pad") as pad:
        picker, fd = _seat_and_open_clone(daemon, sdl, pad)
        try:
            # Closed by hand; nothing in GOTG sends this.
            picker.danstick.send({"cmd": "seating", "open": False})
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
    decompiled port reads the axis straight. Seating is left open, as the
    overlay leaves it for the length of a game: it used to be closed here,
    because gotg-seat closed it, and so the stick was never measured the way
    it is now played.
    """
    import os
    import select

    with FakePad("PERF Pad") as pad:
        picker, fd = _seat_and_open_clone(daemon, sdl, pad)
        try:
            time.sleep(1.0)

            def drain_all() -> list[int]:
                """Everything waiting, not one bufferful: a reader that falls
                behind loses events to the kernel's own queue, and that is the
                reader's fault rather than danstick's."""
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
    """Seating open, as the overlay keeps it for the length of every game.

    Seating open is what lets a second player join in the middle of a level.
    It used to cost about 100 ms a press, so gotg-seat closed it before the
    game started -- the one thing GOTG gave up to get the latency back. danstick
    throttled its scan, this strict xfail turned into an XPASS, and the close
    went; now nothing closes it at all, so this is the latency a game has.
    """
    import os

    with FakePad("PERF Pad") as pad:
        picker, fd = _seat_and_open_clone(daemon, sdl, pad)
        try:
            # Asked again, as the overlay asks after a claim: the same hold,
            # so it changes nothing but proves the point.
            picker.danstick.send(Listening().wanted())
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


# --- a controller's keyboard, and its joystick -------------------------------


def test_a_controllers_keyboard_is_held_and_its_joystick_is_not():
    """The leaking input, named, and the one node that must not be held.

    A Steam Controller in lizard mode types Enter when A is pressed, and the
    launch gate took Enter as "start now" -- so pairing one started the game
    with nobody having held anything. The gate is gone; the picker still takes
    keys, so the rule stands: a keyboard that shares a pad's phys is held, and
    the pad's own joystick node is left for danstick to read.
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
                f"hush did not hold {node}; it could still type into the picker"
            )
            # And the pad itself is untouched: grabbing that would take the
            # presses away from danstick, which is the thing reading them.
            joystick = event_node("E2E Xbox Pad")
            assert joystick and joystick.rsplit("/", 1)[-1] not in held
        finally:
            hush.release()


def test_a_seat_takes_the_hold_that_was_asked_for(daemon, sdl):
    """A quarter second was danstick's, and it was too quick.

    Picking a controller up, or resting a thumb on one while reading the
    screen, claimed a seat nobody meant to claim. danstick now takes the length
    on the `seating` command (98fd757), which the overlay sends with the
    theme's `pair_hold`; this is that asked for and measured through the
    daemon, because a field a daemon ignores looks exactly like a field that
    works.
    """
    assert PAIR_HOLD >= 1.0, f"this test is about a deliberate hold, not {PAIR_HOLD}s"

    with FakePad("E2E Xbox Pad") as pad:
        daemon.send(Listening().wanted())
        daemon.drain(1.0)

        # Well past danstick's own default, and well short of what was asked for.
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


def test_the_keyboard_drives_nothing_until_danstick_seats_it(daemon, sdl):
    """The keyboard goes through danstick now, like everything else.

    An unseated keyboard is any device in the room that types, and most
    controllers are one: a Steam Controller in lizard mode typed Enter at the
    launch door and started the game with nobody ready. So no key but the
    space bar that asks for a seat is heard until danstick reports a keyboard
    seat -- and a pad's seat, which is what somebody is most likely to have,
    is not one. `keys.drives` against the real daemon's `state`, before and
    after.
    """
    with FakePad("E2E Xbox Pad") as pad:
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.0)
        pad.hold(BTN_SOUTH, PAIR)
        assert picker.until(lambda p: p.seated(1)), "the pad took no seat"
        picker.run(1.2)

        assert not keys_rule.drives(picker.players, picker.danstick.connected), (
            "a pad seat is not a keyboard seat"
        )
        assert keys_rule.seat_of(picker.players) is None

        # danstick seats it, and the rule turns over.
        picker.danstick.seat_keyboard()
        assert picker.until(
            lambda p: keys_rule.drives(p.players, p.danstick.connected), seconds=6.0
        ), f"danstick seated no keyboard: {picker.players}"
        assert keys_rule.seat_of(picker.players) == 2, f"the keyboard is not player two: {picker.players}"

        # And a daemon that has gone takes the keyboard's seat with it: a
        # picker that cannot hear danstick cannot know who is seated.
        assert not keys_rule.drives(picker.players, connected=False)
        picker.close()


# --- two people pairing at once -----------------------------------------------
#
# One person pairing is a fraction. Two is a queue: both fills have to be on
# screen, and the seats have to go out in the order the buttons went down --
# not in the order the pads were plugged in, which is what the daemon used to
# do and what nobody on a sofa can see. danstick names the pad on every
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
    looked like before danstick named them.
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



def test_the_seat_goes_to_whoever_pressed_first(daemon, sdl):
    """Not to whichever pad was plugged in first, which is what the daemon
    used to do when two holds finished in the same tick.

    It was a strict xfail until danstick bf6606d: the first claim's
    `seating.reset()` cleared the other pad's hold, and a button already down
    sends no new edge to restart it.
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
