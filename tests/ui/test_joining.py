"""Two people pairing at once, which is a queue rather than a fraction.

One person pairing is a number between nought and one. Two are two fills in
the order the buttons went down, because that is the order the seats go out
in and being second is a thing somebody has to be able to see.
"""

from __future__ import annotations

import pytest

from gotg_ui.joining import NAMED_STALE, STALE, Joining


def reading(frac, node=None, player=None, name=None):
    event = {"event": "progress", "frac": frac}
    if node:
        event["node"] = node
    if player is not None:
        event["player"] = player
    if name:
        event["name"] = name
    return event


def test_two_holds_are_drawn_in_the_order_they_started():
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.02, "event9", 1), 100.00)
    joining.saw(reading(0.02, "event12", 2), 100.01)
    joining.saw(reading(0.04, "event9", 1), 100.02)
    joining.saw(reading(0.03, "event12", 2), 100.03)
    order = [hold.player for hold in joining.now(100.04)]
    assert order == [1, 2], "the pad that pressed first is not first"


def test_the_one_that_pressed_first_stays_first_even_when_it_is_behind():
    # Fractions are not an order: a pad whose readings are late is still the
    # one that pressed first, and a row that reshuffled itself mid-hold would
    # be unreadable.
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.30, "event9", 1), 100.00)
    joining.saw(reading(0.80, "event12", 2), 100.01)
    assert [hold.player for hold in joining.now(100.02)] == [1, 2]


def test_letting_go_loses_the_place():
    """A release said out loud, which is the one thing silence cannot tell
    apart when two pads are holding."""
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.50, "event9", 1), 100.00)
    joining.saw(reading(0.40, "event12", 2), 100.00)
    joining.saw(reading(0.0, "event9"), 100.01)
    left = joining.now(100.02)
    assert [hold.player for hold in left] == [2], f"the released pad is still filling: {left}"


def test_silence_also_ends_a_hold_because_an_old_daemon_says_nothing():
    # Nameless, because that is what an old daemon sends: one that names its
    # pads also says `frac: 0` on release, and its silence is a rescan.
    joining = Joining(hold_seconds=1.5)
    joining.saw({"event": "progress", "frac": 0.50}, 100.00)
    assert joining.now(100.02), "a reading two hundredths old is still a hold"
    assert joining.now(100.30) == [], "the readings stopped and the fill did not"


def test_a_fill_that_goes_backwards_is_a_fresh_press_and_goes_to_the_back():
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.02, "event9", 1), 100.00)
    joining.saw(reading(0.02, "event12", 2), 100.01)
    # event9 let go and pressed again: its fill restarts, and so does its
    # place in the queue.
    joining.saw(reading(0.30, "event12", 2), 100.02)
    joining.saw(reading(0.01, "event9", 1), 100.03)
    assert [hold.player for hold in joining.now(100.04)] == [2, 1]


def test_a_daemon_that_names_no_pads_still_draws_one_fill():
    """What danstick does today: one anonymous fraction per pad per tick.

    Two holds arrive as one number jumping about, and the honest drawing of
    that is the single fill the picker has always had -- not two fills of a
    made-up order. docs/requests/two-people-pairing-at-once.md asks for the
    name; this is what happens until it lands.
    """
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.20), 100.00)
    joining.saw(reading(0.25), 100.01)
    held = joining.now(100.02)
    assert len(held) == 1
    assert held[0].player is None


def test_a_fill_keeps_moving_between_readings():
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.30, "event9", 1), 100.00)
    later = joining.now(100.015)[0].fraction
    assert 0.30 < later < 0.32, f"the fill sat still between readings at {later}"


def test_a_claim_clears_the_queue_because_the_state_is_the_truth_after_one():
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.90, "event9", 1), 100.00)
    joining.clear()
    assert joining.now(100.01) == []


def test_a_state_while_somebody_is_still_holding_keeps_their_fill():
    """The flash: danstick restates the world mid-hold and the reveal blinked.

    A `state` arrives for all sorts of reasons -- a pad appearing, a
    republish, the daemon's own tick -- and the queue used to be wiped by
    every one of them. The next reading twenty milliseconds later started the
    fill again from nothing, so a controller filling in went back to empty
    several times in one hold.
    """
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.6, "player": 2}, 10.0)
    queue.seated([{"player": 1, "name": "Xbox 360 Controller", "node": "/dev/input/event3"}])
    holds = queue.now(10.0)
    assert len(holds) == 1, "a hold still climbing was thrown away by somebody else's seat"
    assert holds[0].fraction == pytest.approx(0.6)


def test_and_drops_the_hold_that_has_become_a_seat():
    """What a state does settle: the fill that got there is over."""
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.99, "player": 1}, 10.0)
    queue.seated([{"player": 1, "name": "Xbox 360 Controller", "node": "/dev/input/event9"}])
    assert queue.now(10.0) == []


def test_a_seat_matched_by_its_node_when_the_hold_never_said_a_player():
    """danstick names the pad before it knows which seat it is filling."""
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.4}, 10.0)
    queue.seated([{"player": 1, "name": "Pad", "node": "/dev/input/event9"}])
    assert queue.now(10.0) == []


def test_a_stale_seat_number_does_not_blink_the_second_persons_fill():
    """Two at once, which is the whole reason this queue exists.

    danstick's reading names the seat a hold is filling towards, and that
    number is stale for a tick after somebody else's claim lands: the second
    person's reading still says seat one while the daemon works out that they
    are seat two. Identity is what settles it -- their pad is not the pad in
    the seat -- and the number is only read when there is nothing else.
    """
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.5, "player": 1}, 10.0)
    queue.seated([{"player": 1, "name": "First Pad", "node": "/dev/input/event3"}])
    holds = queue.now(10.0)
    assert len(holds) == 1, "the second person's fill went with somebody else's claim"
    assert holds[0].fraction == pytest.approx(0.5)


def test_an_anonymous_hold_goes_when_its_seat_is_taken():
    """With no name on the reading the seat number is the only thing there."""
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "frac": 0.9, "player": 1}, 10.0)
    queue.seated([{"player": 1, "name": "Pad", "node": "/dev/input/event9"}])
    assert queue.now(10.0) == []


def test_a_named_hold_survives_the_daemon_pausing_to_rescan():
    """The flash: danstick sends progress from the same loop that rescans every
    device once a second, and a rescan outlasts fifty milliseconds. The hold
    was dropped mid-press -- the red empty seat, then the controller again.
    A named reading's release is said out loud, so silence is not one."""
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.3}, 10.0)
    # A 120 ms rescan: nothing arrives, and every frame still draws the fill.
    for frame in range(1, 8):
        holds = queue.now(10.0 + frame * 0.016)
        assert len(holds) == 1, f"the hold vanished {frame * 16} ms into a pause"
    # Carried forward at the hold's own rate through the gap, not frozen.
    assert queue.now(10.12)[0].fraction == pytest.approx(0.3 + 0.12 / 1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.38}, 10.12)
    assert len(queue.now(10.13)) == 1


def test_a_named_release_is_immediate_whatever_the_safety_net():
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.5}, 10.0)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0}, 10.02)
    assert queue.now(10.02) == []


def test_a_daemon_that_dies_mid_hold_still_empties_the_seat_eventually():
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.5}, 10.0)
    assert queue.now(10.0 + NAMED_STALE - 0.01)
    assert queue.now(10.0 + NAMED_STALE + 0.01) == []


def test_a_nameless_reading_still_ends_with_silence():
    """An older daemon says nothing when a button comes up, so for it three
    frames of silence is still the release."""
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "frac": 0.5}, 10.0)
    assert queue.now(10.0 + STALE + 0.01) == []


def test_once_readings_are_named_the_nameless_fill_is_not_drawn_as_well():
    """One press drawn twice, in two places, taking turns: the named hold in
    the queue and the anonymous fill beside it."""
    queue = Joining(hold_seconds=1.5)
    assert queue.anonymous(0.4) == 0.4
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.4}, 10.0)
    assert queue.anonymous(0.4) == 0.0


def test_the_sweep_never_ticks_backwards_when_a_reading_lands_behind_it():
    """Carried forward through a pause, then a real reading a hair behind
    where the drawing had got to: the edge of the reveal stepping back."""
    queue = Joining(hold_seconds=1.5)
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.30}, 10.0)
    ahead = queue.now(10.10)[0].fraction  # carried to ~0.367
    queue.saw({"event": "progress", "node": "/dev/input/event9", "frac": 0.36}, 10.10)
    assert queue.now(10.10)[0].fraction >= ahead


def test_the_keyboard_joining_is_drawn_as_the_keyboard_and_mouse():
    # danstick times the space bar itself and reports it as a pad's hold, with
    # the seat's name and no node (docs/EVENTS.md, danstick e0092be): the strip
    # and the gate have to file it by name and draw the desk.
    from gotg_ui.icons import icon_name

    joining = Joining()
    joining.saw({"event": "progress", "frac": 0.4, "name": "Keyboard and Mouse", "node": "", "player": 2}, 10.0)
    [hold] = joining.now(10.0)
    assert hold.name == "Keyboard and Mouse"
    assert icon_name(hold.name) == "keyboard-mouse"
    joining.saw({"event": "progress", "frac": 0, "name": "Keyboard and Mouse", "node": "", "player": None}, 10.1)
    assert joining.now(10.1) == [], "let go early, it goes at once"
