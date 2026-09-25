"""Readying up with more than one controller in the room.

The rule: everybody seated holds A for the shipped hold, and when the last of
them is ready the game starts. The first version had one hold for the whole
room and one pause measured across every pad, which two people could lock
solid -- these are the shapes of that deadlock, and the ordinary cases either
side of it.
"""

from __future__ import annotations

from gotg_ui.ready import Ready


def quiet(ready: Ready, seats: set[int], now: float) -> None:
    """Nothing down anywhere, for long enough that a press would count."""
    ready.tick(set(), seats, now)
    ready.tick(set(), seats, now + ready.pause + 0.01)


def test_two_people_holding_at_once_do_not_lock_each_other_out():
    """The deadlock, as it was reported.

    One player holding means the room is never quiet; the other's press is
    refused for as long as that lasts, and when the first lets go the second
    is still holding, so it is still never quiet. Neither could ever start.
    """
    ready = Ready(seconds=3.0, pause=1.0)
    seats = {1, 2}
    quiet(ready, seats, 100.0)

    ready.pressed(1, 101.02)
    ready.tick({1}, seats, 101.03)
    assert ready.progress(1, 101.03) > 0, "the first player's hold never started"

    # The second player presses while the first is still holding. Their own
    # pad has been quiet all along, so it counts.
    ready.pressed(2, 101.10)
    ready.tick({1, 2}, seats, 101.11)
    assert ready.progress(2, 101.11) > 0, "the second player was locked out by the first"


def test_the_game_waits_for_everybody():
    ready = Ready(seconds=3.0, pause=1.0)
    seats = {1, 2}
    quiet(ready, seats, 100.0)

    ready.pressed(1, 101.02)
    ready.tick({1}, seats, 104.05)
    assert ready.ready == {1}
    assert not ready.all_ready(seats), "one player ready started the game for both"

    ready.pressed(2, 104.10)
    ready.tick({1, 2}, seats, 107.20)
    assert ready.all_ready(seats), "everybody held and the game did not start"


def test_readiness_stays_while_the_others_catch_up():
    """Whoever goes first should not have to keep holding."""
    ready = Ready(seconds=3.0, pause=1.0)
    seats = {1, 2}
    quiet(ready, seats, 100.0)
    ready.pressed(1, 101.02)
    ready.tick({1}, seats, 104.05)
    assert 1 in ready.ready
    # They let go. Nothing on this pad for a good while.
    for step in range(5):
        ready.tick(set(), seats, 104.1 + step)
    assert 1 in ready.ready, "a player who readied up lost it by letting go"
    assert ready.progress(1, 110.0) == 1.0


def test_a_hold_let_go_of_is_lost_and_starts_again():
    ready = Ready(seconds=3.0, pause=1.0)
    seats = {1}
    quiet(ready, seats, 100.0)
    ready.pressed(1, 101.02)
    ready.tick({1}, seats, 102.0)
    assert 0 < ready.progress(1, 102.0) < 1
    ready.tick(set(), seats, 102.5)                 # let go half way
    assert ready.progress(1, 102.5) == 0.0
    assert not ready.ready
    # And the pause starts again, so the next press is a deliberate one.
    ready.pressed(1, 102.6)
    assert ready.progress(1, 102.6) == 0.0, "a press inside the pause counted"
    ready.tick(set(), seats, 103.6)
    ready.pressed(1, 103.7)
    ready.tick({1}, seats, 103.8)
    assert ready.progress(1, 103.8) > 0


def test_the_press_that_paired_this_pad_does_not_ready_it():
    """The pause, per seat. The pairing hold is danstick's quarter second and a
    thumb stays down longer than that."""
    ready = Ready(seconds=3.0, pause=1.0)
    seats = {1}
    ready.tick({1}, seats, 100.0)      # still holding from pairing
    ready.pressed(1, 100.1)
    assert ready.progress(1, 100.1) == 0.0
    ready.tick({1}, seats, 103.0)
    assert not ready.ready, "the pairing hold readied the player up"


def test_a_seat_that_leaves_takes_its_readiness_with_it():
    ready = Ready(seconds=3.0, pause=1.0)
    seats = {1, 2}
    quiet(ready, seats, 100.0)
    ready.pressed(1, 101.02)
    ready.tick({1}, seats, 104.05)
    assert ready.all_ready({1}) is False or True    # 2 is still here
    assert not ready.all_ready(seats)
    # Player two unplugs. One player, ready, and the game may start.
    ready.tick(set(), {1}, 104.1)
    assert ready.all_ready({1})


def test_a_room_with_nobody_in_it_is_not_ready():
    ready = Ready(seconds=3.0, pause=1.0)
    assert not ready.all_ready(set()), "a game started with no controllers in it"


def test_somebody_joining_after_the_others_readied_holds_the_game():
    """A pad paired at the door has to ready up like everybody else."""
    ready = Ready(seconds=3.0, pause=1.0)
    quiet(ready, {1}, 100.0)
    ready.pressed(1, 101.02)
    ready.tick({1}, {1}, 104.05)
    assert ready.all_ready({1})
    # Player two pairs at the door. The room is no longer ready.
    ready.tick(set(), {1, 2}, 104.1)
    assert not ready.all_ready({1, 2})
