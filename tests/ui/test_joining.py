"""Two people pairing at once, which is a queue rather than a fraction.

One person pairing is a number between nought and one. Two are two fills in
the order the buttons went down, because that is the order the seats go out
in and being second is a thing somebody has to be able to see.
"""

from __future__ import annotations

from gotg_ui.joining import Joining


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
    joining = Joining(hold_seconds=1.5)
    joining.saw(reading(0.50, "event9", 1), 100.00)
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
    """What padmap does today: one anonymous fraction per pad per tick.

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
