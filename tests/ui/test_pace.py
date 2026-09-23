"""The frame pacing, which is where the picker's jitter actually was.

A trace from a real evening: 1.4 ms drawing, 1.1 ms presenting, 13.4 ms asleep
in `clock.tick(60)`, 62 frames a second, on a 165 Hz panel. Nothing waited for
the panel, so frames landed two or three refreshes apart, unevenly.
"""

from __future__ import annotations

import pytest

from gotg_ui.pace import IDLE_AFTER, PROBE, Pace, every, wait


@pytest.mark.parametrize(
    ("refresh", "most", "expected"),
    [
        (165, 125, 2),  # the panel the jitter was seen on: every second refresh
        (144, 125, 2),
        (120, 125, 1),
        (90, 125, 1),  # a Deck OLED
        (60, 125, 1),
        (60, 30, 2),  # the install log's own ceiling
        (165, 30, 6),
        (0, 125, 1),  # a driver that will not say
    ],
)
def test_a_frame_is_a_whole_number_of_refreshes(refresh, most, expected):
    """60 fps on a 165 Hz panel is 2.75 refreshes a frame, which is three and
    then two and then three: the stutter. A whole number never is."""
    assert every(refresh, most) == expected


def _probe(pace: Pace, interval: float, start: float = 100.0) -> float:
    now = start
    for _ in range(PROBE + 1):
        pace.presented(now)
        now += interval
    return now


def test_a_present_that_waits_for_the_panel_is_believed():
    pace = Pace(refresh=165)
    _probe(pace, 1 / 165)
    assert pace.holds is True


def test_a_present_that_returns_at_once_is_not_vsync_whatever_the_config_says():
    """The trace's own case: `vsync: true` in the config, a present of a
    millisecond, and a loop timed by nothing but its own sleep."""
    pace = Pace(refresh=165)
    _probe(pace, 0.0025)
    assert pace.holds is False


def test_while_probing_frames_go_back_to_back():
    pace = Pace(refresh=165)
    pace.busy(100.0)
    pace.presented(100.0)
    assert pace.rest(100.0) == 0.0


def test_waited_for_it_sleeps_the_refreshes_in_between():
    """Every second refresh on 165 Hz: sleep one, and the present that follows
    lands on the next -- the same length every frame."""
    pace = Pace(refresh=165)
    now = _probe(pace, 1 / 165)
    pace.busy(now)
    assert pace.rest(now) == pytest.approx(1 / 165)


def test_every_refresh_means_no_sleep_at_all():
    pace = Pace(refresh=60)
    now = _probe(pace, 1 / 60)
    pace.busy(now)
    assert pace.rest(now) == 0.0


def _timed_frames(pace: Pace, start: float, work: float, frames: int = 30) -> list[float]:
    """Run a loop the way the picker does -- draw, present, rest -- and say
    how far apart the frames started."""
    starts, now = [], start
    for _ in range(frames):
        starts.append(now)
        now += work
        pace.presented(now)
        pace.busy(now)
        now += pace.rest(now)
    return [b - a for a, b in zip(starts, starts[1:], strict=False)]


def test_not_waited_for_it_falls_back_to_a_timed_frame():
    pace = Pace(refresh=60)
    now = _probe(pace, 0.002)
    gaps = _timed_frames(pace, now, work=0.0025)
    assert all(gap == pytest.approx(1 / 60) for gap in gaps[1:]), gaps[:6]


def test_timed_frames_are_all_the_same_length():
    """The trace: 2.6 ms, 16.7 ms, 2.6 ms, 16.7 ms -- 103 fps, and as uneven
    as the stutter it replaced. Every frame the same length or it is not
    pacing at all."""
    pace = Pace(refresh=165)
    now = _probe(pace, 0.0025)
    gaps = _timed_frames(pace, now, work=0.0025)
    assert max(gaps[1:]) - min(gaps[1:]) < 1e-9, gaps[:6]
    # And a whole number of the panel's refreshes: two of them, not 2.75.
    assert gaps[-1] == pytest.approx(2 / 165)


def test_a_slow_frame_does_not_make_the_next_ones_rush():
    pace = Pace(refresh=60)
    now = _probe(pace, 0.002)
    _timed_frames(pace, now, work=0.002, frames=5)
    # One frame that took a tenth of a second...
    later = now + 1.0
    pace.presented(later)
    pace.busy(later)
    # ...and the schedule starts again from there, a whole frame on.
    assert pace.rest(later) == pytest.approx(1 / 60)


def test_the_window_s_own_panel_sets_the_rate():
    """Three monitors at 60, 165 and 165: the first one's rate was used
    wherever the window was."""
    pace = Pace(refresh=60)
    now = _probe(pace, 0.002)
    pace.moved(165)
    gaps = _timed_frames(pace, now, work=0.002)
    assert gaps[-1] == pytest.approx(2 / 165)


def test_a_measured_panel_is_not_overruled():
    pace = Pace(refresh=165)
    _probe(pace, 1 / 144)
    pace.moved(60)
    assert pace.refresh == pytest.approx(144, rel=0.01)


def test_a_screen_nobody_has_touched_redraws_a_few_times_a_second():
    pace = Pace(refresh=165)
    now = _probe(pace, 1 / 165)
    pace.busy(now)
    assert not pace.idle(now + IDLE_AFTER / 2)
    assert pace.idle(now + IDLE_AFTER + 0.01)
    assert pace.rest(now + IDLE_AFTER + 0.01) == pytest.approx(0.1)


def test_and_anything_happening_wakes_it_back_to_full_rate():
    pace = Pace(refresh=60)
    now = _probe(pace, 1 / 60)
    assert pace.idle(now + 5)
    pace.busy(now + 5)
    assert not pace.idle(now + 5)
    assert pace.rest(now + 5) == 0.0


def test_a_caller_can_ask_for_less():
    """The install log redraws a mostly unchanged tail; 30 is plenty."""
    pace = Pace(refresh=60)
    now = _probe(pace, 1 / 60)
    pace.busy(now)
    assert pace.rest(now, cap=30) == pytest.approx(1 / 60)


def test_vsync_turned_off_is_timed_from_the_first_frame():
    pace = Pace(refresh=60, vsync=False)
    pace.busy(100.0)
    pace.presented(100.0)
    assert pace.holds is False
    assert pace.rest(100.0) == pytest.approx(1 / 60)


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def test_an_idle_wait_ends_the_moment_a_button_arrives():
    """The first press after a quiet minute must not be the one that waits."""
    clock = _Clock()
    arrived = iter([False, False, True])
    wait(0.1, peek=lambda: next(arrived), sleep=clock.sleep, clock=clock)
    assert clock.now < 0.02


def test_and_the_daemon_speaking_wakes_it_too():
    clock = _Clock()
    wait(0.1, woken=lambda: clock.now > 0.01, peek=lambda: False, sleep=clock.sleep, clock=clock)
    assert clock.now < 0.02


def test_a_short_rest_is_one_sleep_so_it_lands_on_time():
    clock = _Clock()
    wait(1 / 165, peek=lambda: False, sleep=clock.sleep, clock=clock)
    assert clock.slept == [pytest.approx(1 / 165)]


def test_a_window_that_opens_idle_still_probes_at_full_speed():
    """Nothing has happened yet when a window opens -- which is idle -- and a
    tenth of a second between probe frames reads exactly like a panel that
    waits. The probe has to run back to back regardless."""
    pace = Pace(refresh=165)
    pace.presented(100.0)
    assert pace.idle(100.0)
    assert pace.rest(100.0) == 0.0


def test_the_probe_measures_the_panel_rather_than_trusting_the_desktop():
    """Three monitors: the desktop's first rate is the first monitor's, and
    the window is not always on it. A panel that waits says its own rate."""
    pace = Pace(refresh=165)
    _probe(pace, 1 / 60)
    assert pace.holds is True
    assert pace.refresh == pytest.approx(60, rel=0.01)
