"""Where a frame's time went.

"The UI runs at pretty low FPS" is not actionable: drawing and presenting
fail for different reasons and are fixed in different places. These pin the
arithmetic that tells them apart.
"""

from __future__ import annotations

from gotg_ui.meter import Meter


def test_nothing_is_said_until_a_second_has_passed():
    meter = Meter()
    assert meter.frame(0.004, 0.002, 0.010, 100.0) is None
    assert meter.frame(0.004, 0.002, 0.010, 100.5) is None


def test_a_second_of_frames_is_averaged_and_the_worst_one_kept():
    meter = Meter()
    for step in range(11):
        # One frame in eleven is a bad one, and an average alone hides it.
        drawing = 0.050 if step == 5 else 0.004
        said = meter.frame(drawing, 0.002, 0.010, 100.0 + step * 0.1)
    assert said is not None, "a second of frames said nothing"
    assert said["fps"] == 11.0
    assert said["draw_ms"] == 8.18, "eleven frames, one of them fifty milliseconds"
    assert said["present_ms"] == 2.0
    assert said["worst_ms"] == 52.0, "the worst frame is the one somebody felt"


def test_it_starts_again_after_it_has_spoken():
    meter = Meter()
    for step in range(12):
        meter.frame(0.004, 0.002, 0.010, 100.0 + step * 0.1)
    assert meter.frames <= 1 and meter.worst <= 0.006, "the second did not start over"


def test_idle_is_counted_because_a_slow_frame_that_waits_is_not_this_programs_fault():
    """A frame capped at 60 spends most of itself asleep. Saying so is what
    stops "low FPS" being read as "drawing is slow"."""
    meter = Meter()
    said = None
    for step in range(61):
        said = meter.frame(0.001, 0.001, 0.0146, 100.0 + step / 60)
    assert said is not None
    assert said["idle_ms"] > said["draw_ms"] + said["present_ms"]
