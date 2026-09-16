"""Seats along the top: who is sitting where, and in which colour.

The drawing needs a screen and is not tested here. What is tested is the part
that decides what gets drawn -- which seat is empty, what a seat is called, and
that the colours stay put, since a player learns their colour rather than
reading it.
"""

from __future__ import annotations

from gotg_ui.padstrip import PLAYER_COLOURS, colour_for, name_for, seats, status_text


def player(n: int, name: str = "", **extra) -> dict:
    return {"player": n, "name": name or f"padmap Player {n}", **extra}


def test_four_seats_even_when_nobody_is_in_them():
    # The empty ones are the useful part: a pad that did not come back after a
    # replug is a seat that went grey, not a strip with one fewer thing on it.
    assert seats([], 4) == [None, None, None, None]


def test_a_gap_stays_a_gap():
    got = seats([player(1), player(3)], 4)
    assert [s["player"] if s else None for s in got] == [1, None, 3, None]


def test_more_players_than_seats_asked_for_still_all_show():
    got = seats([player(1), player(2), player(3)], 2)
    assert len([s for s in got if s]) == 3


def test_every_player_has_their_own_colour():
    assert len({colour_for(n) for n in (1, 2, 3, 4)}) == 4


def test_the_colours_do_not_move():
    # Learned, not read: player two is red on the strip, on a name tag and in
    # the split-screen frame, and they agree because this list does not change.
    assert colour_for(1) == PLAYER_COLOURS[0]
    assert colour_for(4) == PLAYER_COLOURS[3]


def test_a_fifth_player_wraps_rather_than_failing():
    assert colour_for(5) == colour_for(1)


def test_an_empty_seat_says_so():
    assert name_for(None) == "empty"


def test_padmap_s_own_name_is_not_what_gets_drawn():
    # "padmap Player 2" beside a badge already saying 2, in player two's
    # colour, is the same fact three times and the controller not once.
    assert name_for(player(2, "padmap Player 2", model="N64 adapter")) == "N64 adapter"


def test_a_pad_with_a_real_name_keeps_it():
    assert name_for(player(1, "Xbox 360 Controller")) == "Xbox 360 Controller"


def test_a_nameless_pad_still_reads_as_something():
    assert name_for({"player": 1}) == "pad"
    assert name_for(player(1, "padmap Player 1")) == "pad"


def test_the_status_is_in_words_somebody_can_act_on():
    assert status_text("assigning") == "hold a button on each controller"
    assert status_text("offline") == "padmap not running"
    # An unknown state is shown rather than swallowed: a daemon that grew a
    # state this does not know about should not draw an empty corner.
    assert status_text("recalibrating") == "recalibrating"


def test_an_idle_padmap_with_nobody_seated_says_how_to_start():
    # "padmap ready" is true and useless there: the question in front of
    # somebody is how to make it do anything, not what it is doing.
    from gotg_ui.padstrip import strip_status

    assert strip_status("idle", 0) == "press C to assign controllers"


def test_once_somebody_is_seated_it_goes_back_to_saying_what_is_true():
    from gotg_ui.padstrip import strip_status

    assert strip_status("idle", 2) == "padmap ready"
    assert strip_status("assigning", 0) == "hold a button on each controller"
