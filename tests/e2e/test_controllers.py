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
        strict, listen = attend(
            self.padmap, self.seating, self.watch, padmap_here=True
        )
        pads.only_padmap(strict)
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

        first.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(1)), "the first pad took no seat"
        second.hold(BTN_SOUTH, 0.7)
        assert picker.until(lambda p: p.seated(2)), "the second pad took no seat"

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


def test_the_keyboard_is_never_filtered(sdl):
    """Two things drive the picker, and the other one is the keyboard.

    The filter is only ever asked about pads: a key is not a pad event and
    cannot be swallowed by it, whatever padmap is doing.
    """
    pads.only_padmap(True)
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


@pytest.mark.xfail(
    strict=True,
    reason="padmap has no way to unseat; see docs/requests/session-daemon.md",
)
def test_a_new_session_opens_with_nobody_seated_whatever_padmap_remembers(daemon, sdl):
    """Open the picker, or a game: nobody is seated until somebody holds a button.

    Today the daemon restores yesterday's seats on start and offers no
    command to drop them, so this fails -- strictly, so the day the request
    is answered it fails the other way until the marker comes off.
    """
    with FakePad("E2E Xbox Pad") as pad:
        earlier = Picker(_socket(daemon), sdl)
        earlier.run(1.0)
        pad.hold(BTN_SOUTH, 0.7)
        assert earlier.until(lambda p: p.seated(1))
        earlier.close()

        # A new session: the picker again, or a game. Same daemon, same pad.
        picker = Picker(_socket(daemon), sdl)
        picker.run(1.5)
        assert picker.players == [], (
            f"the session opened with seats already taken: {picker.players}"
        )
        picker.close()
