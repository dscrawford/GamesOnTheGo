"""Behaviour of gotg_ui.nav.Nav; its module docstring says why it exists."""

from __future__ import annotations

from gotg_ui.nav import DELAY, INTERVAL, Nav

PAD = 7


def steps(nav: Nav, now: float) -> tuple[Nav, list[tuple[int, int]]]:
    nav, due = nav.due(now)
    return nav, [direction for _, direction in due]


def test_a_pushed_stick_steps_at_once():
    nav, step = Nav().stick(PAD, 0, 0.9, 10.0)
    assert step == (1, 0), "right, as the push arrives"


def test_a_small_push_is_not_a_step():
    nav, step = Nav().stick(PAD, 0, 0.3, 10.0)
    assert step is None
    assert nav.due(10.0 + DELAY + 1.0)[1] == [], "and repeats nothing"


def test_a_held_stick_repeats_after_a_pause():
    nav, _ = Nav().stick(PAD, 1, -0.95, 10.0)
    nav, early = steps(nav, 10.0 + DELAY - 0.01)
    assert early == [], "nothing before the pause is over"
    nav, first = steps(nav, 10.0 + DELAY)
    assert first == [(0, -1)], "then up again"
    nav, second = steps(nav, 10.0 + DELAY + INTERVAL)
    assert second == [(0, -1)], "and again every interval"


def test_a_slow_frame_does_not_bunch_repeats_into_a_jump():
    # A frame that came a second late must not scroll six tiles at once.
    nav, _ = Nav().stick(PAD, 0, 0.9, 10.0)
    nav, late = steps(nav, 10.0 + DELAY + 1.0)
    assert late == [(1, 0)]


def test_letting_the_stick_back_stops_the_repeat():
    nav, _ = Nav().stick(PAD, 0, 0.9, 10.0)
    nav, step = nav.stick(PAD, 0, 0.1, 10.1)
    assert step is None
    assert nav.due(10.0 + DELAY + 1.0)[1] == []


def test_a_stick_resting_near_the_edge_does_not_chatter():
    # Past the threshold it steps; it has to come well back before a push
    # counts as a new one, or a thumb resting at 0.5 steps over and over.
    nav, first = Nav().stick(PAD, 0, 0.6, 10.0)
    nav, wobble = nav.stick(PAD, 0, 0.45, 10.02)
    nav, again = nav.stick(PAD, 0, 0.6, 10.04)
    assert first == (1, 0) and wobble is None and again is None


def test_the_stronger_axis_wins_a_diagonal():
    nav, _ = Nav().stick(PAD, 0, 0.6, 10.0)
    nav, step = nav.stick(PAD, 1, 0.9, 10.01)
    assert step == (0, 1), "turning to down is a new direction"


def test_a_held_d_pad_repeats_but_its_press_is_not_counted_twice():
    # The press itself already moved the grid; the model only repeats it.
    nav = Nav().press(PAD, (0, 1), 10.0)
    assert nav.due(10.0 + DELAY - 0.01)[1] == []
    nav, repeated = steps(nav, 10.0 + DELAY)
    assert repeated == [(0, 1)]
    nav = nav.release(PAD, (0, 1))
    assert nav.due(12.0)[1] == [], "let go, it stops"


def test_two_pads_repeat_on_their_own():
    nav, _ = Nav().stick(1, 0, 0.9, 10.0)
    nav = nav.press(2, (0, -1), 10.2)
    nav, due = nav.due(10.2 + DELAY)
    assert sorted(due) == [(1, (1, 0)), (2, (0, -1))]


def test_a_pad_that_goes_away_stops_repeating():
    nav, _ = Nav().stick(PAD, 0, 0.9, 10.0)
    assert nav.forget(PAD).due(12.0)[1] == []


def test_the_loop_is_told_when_it_must_keep_running():
    assert not Nav().holding()
    nav, _ = Nav().stick(PAD, 0, 0.9, 10.0)
    assert nav.holding(), "a held direction needs frames to repeat in"


def test_only_the_left_stick_steers():
    for axis in (2, 3, 4, 5, -1):
        nav, step = Nav().stick(PAD, axis, 0.9, 10.0)
        assert step is None and not nav.holding(), f"axis {axis}"


def test_a_stick_flipped_straight_over_is_a_new_push_with_its_own_pause():
    nav, _ = Nav().stick(PAD, 0, 0.9, 10.0)
    nav, flipped = nav.stick(PAD, 0, -0.9, 10.05)
    assert flipped == (-1, 0)
    assert nav.due(10.05 + DELAY - 0.01)[1] == [], "the pause starts again"
    assert nav.due(10.05 + DELAY)[1] == [(PAD, (-1, 0))]


def test_asking_twice_in_one_instant_repeats_once():
    nav, _ = Nav().stick(PAD, 0, 0.9, 10.0)
    nav, first = steps(nav, 10.0 + DELAY)
    nav, second = steps(nav, 10.0 + DELAY)
    assert (first, second) == ([(1, 0)], [])


def test_a_stale_release_does_not_stop_the_direction_held_now():
    # Up is re-pressed as right before up's own release arrives.
    nav = Nav().press(PAD, (0, -1), 10.0).press(PAD, (1, 0), 10.05).release(PAD, (0, -1))
    assert steps(nav, 10.05 + DELAY)[1] == [(1, 0)]


def test_forgetting_a_pad_never_seen_changes_nothing():
    assert not Nav().forget(PAD).holding()
