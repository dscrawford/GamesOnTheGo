"""Seats along the top: who is sitting where, and in which colour.

The drawing needs a screen and is not tested here. What is tested is the part
that decides what gets drawn -- which seat is empty, what a seat is called, and
that the colours stay put, since a player learns their colour rather than
reading it.
"""

from __future__ import annotations

from gotg_ui.padstrip import PLAYER_COLOURS, colour_for, name_for, next_seat, seats, status_text


def player(n: int, name: str = "", **extra) -> dict:
    return {"player": n, "name": name or f"danstick Player {n}", **extra}


def test_nobody_connected_is_no_seats_at_all():
    # Three red rings saying "still nobody" above the games is a permanent
    # complaint about a machine working exactly as it should with one pad. The
    # strip draws an X instead, once.
    assert seats([], 4) == []


def test_only_the_seats_somebody_is_in():
    got = seats([player(1), player(3)], 4)
    assert [n for n, _ in got] == [1, 3]


def test_a_seat_keeps_its_own_number():
    # Sliding 3 into the second place would tell somebody they are player two
    # when every emulator on the machine thinks otherwise.
    got = seats([player(3)], 4)
    assert [n for n, _ in got] == [3]


def test_more_players_than_seats_asked_for_still_all_show():
    got = seats([player(1), player(2), player(3)], 2)
    assert len(got) == 3


def test_every_player_has_their_own_colour():
    assert len({colour_for(n) for n in (1, 2, 3, 4)}) == 4


def test_the_colours_do_not_move():
    # Learned, not read: player two is red on the strip, on a name tag and in
    # the split-screen frame, and they agree because this list does not change.
    assert colour_for(1) == PLAYER_COLOURS[0]
    assert colour_for(4) == PLAYER_COLOURS[3]


def test_a_fifth_player_wraps_rather_than_failing():
    assert colour_for(5) == colour_for(1)


def test_an_empty_seat_writes_nothing():
    # Nothing draws one any more, but the helper still answers for it rather
    # than raising: the assignment screen asks about seats nobody is in.
    assert name_for(None) == ""


def test_danstick_s_own_name_is_not_what_gets_drawn():
    # "danstick Player 2" beside a badge already saying 2, in player two's
    # colour, is the same fact three times and the controller not once.
    assert name_for(player(2, "danstick Player 2", model="N64 adapter")) == "N64 adapter"


def test_a_pad_with_a_real_name_keeps_it():
    assert name_for(player(1, "Xbox 360 Controller")) == "Xbox 360 Controller"


def test_a_nameless_pad_still_reads_as_something():
    assert name_for({"player": 1}) == "pad"
    assert name_for(player(1, "danstick Player 1")) == "pad"


def test_the_status_is_in_words_somebody_can_act_on():
    assert status_text("assigning") == "hold a button on each controller"
    assert status_text("offline") == "danstick not running"
    # An unknown state is shown rather than swallowed: a daemon that grew a
    # state this does not know about should not draw an empty corner.
    assert status_text("recalibrating") == "recalibrating"


def test_an_idle_danstick_with_nobody_seated_says_how_to_start():
    # "danstick ready" is true and useless there: the question in front of
    # somebody is how to make it do anything, not what it is doing. And what
    # it takes is the hold danstick is already listening for, not a key on a
    # keyboard nobody carried to the sofa.
    from gotg_ui.padstrip import strip_status

    assert strip_status("idle", 0) == "hold a button on a controller, or space"
    assert strip_status("ready", 0) == "hold a button on a controller, or space"


def test_once_somebody_is_seated_it_goes_back_to_saying_what_is_true():
    from gotg_ui.padstrip import strip_status

    assert strip_status("idle", 2) == "danstick ready"
    assert strip_status("assigning", 0) == "hold a button on each controller"


def test_the_hold_fills_the_first_free_seat():
    assert next_seat([], 4) == 1
    assert next_seat([player(1)], 4) == 2


def test_a_gap_is_filled_before_the_end():
    # Player two unplugged and somebody else picked a pad up: they are two,
    # not five, and the ring belongs where two sits.
    assert next_seat([player(1), player(3)], 4) == 2


def test_no_seat_is_left_to_fill():
    assert next_seat([player(n) for n in (1, 2, 3, 4)], 4) is None


# --- the controller revealed, clockwise from twelve ----------------------------


def test_the_reveal_starts_at_twelve_and_goes_clockwise():
    # Screen coordinates: y grows downward, so twelve is (0, -r) and three
    # o'clock is (r, 0). A quarter turn sweeps the top-right quadrant.
    from gotg_ui.padstrip import wedge

    points = wedge((0.0, 0.0), 10.0, 0.25)
    assert points[0] == (0.0, 0.0)
    assert points[1] == (0.0, -10.0)
    last = points[-1]
    assert abs(last[0] - 10.0) < 1e-6 and abs(last[1]) < 1e-6
    assert all(x >= -1e-6 and y <= 1e-6 for x, y in points[1:])


def test_half_a_turn_reaches_six():
    from gotg_ui.padstrip import wedge

    x, y = wedge((0.0, 0.0), 10.0, 0.5)[-1]
    assert abs(x) < 1e-6 and abs(y - 10.0) < 1e-6


def test_a_whole_turn_is_the_whole_disc():
    from gotg_ui.padstrip import wedge

    points = wedge((5.0, 5.0), 10.0, 1.0)
    x, y = points[-1]
    assert abs(x - 5.0) < 1e-6 and abs(y + 5.0) < 1e-6
    assert len(points) > 40


def test_nothing_held_is_no_wedge_at_all():
    from gotg_ui.padstrip import wedge

    assert wedge((0.0, 0.0), 10.0, 0.0) == []
    assert wedge((0.0, 0.0), 10.0, -1.0) == []


def test_more_than_everything_is_everything():
    from gotg_ui.padstrip import wedge

    assert wedge((0.0, 0.0), 10.0, 1.5) == wedge((0.0, 0.0), 10.0, 1.0)
