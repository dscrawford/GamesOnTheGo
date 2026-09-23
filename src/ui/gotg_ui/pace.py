"""When to draw the next frame: on the panel's own beat, and seldom when idle.

The picker drew at "sixty frames a second" by sleeping in `clock.tick(60)`,
and the trace from a real evening said what that was worth: 1.4 ms drawing,
1.1 ms presenting, 13.4 ms asleep, 62 frames a second -- on a 165 Hz panel.
Nothing was waiting for the panel at all. A present that takes a millisecond
is not a vsynced one; pygame's plain window cannot vsync, whatever
`theme.vsync` says. So each frame landed two or three refreshes after the one
before, unevenly, and a ring filling or a stick dot moving at a steady speed
arrived on screen in a stutter. That was the jitter, not Python.

The fix has two halves. `display.py` presents through a renderer that really
does wait for the panel. This decides how many of the panel's refreshes one
frame spans -- a whole number, always, because 60 fps on a 165 Hz panel is
2.75 refreshes a frame and there is no such thing -- and sleeps the rest.
165 Hz with the default ceiling is every second refresh, 82.5 frames a second,
every one of them the same length. 60 Hz is every refresh. A Deck is every
refresh.

And when nothing on screen is moving, the loop stops drawing sixty times a
second a picture that has not changed. `idle` is a few frames a second,
woken at once by anything SDL delivers -- a key, a button, a pad arriving --
so the first press after a quiet minute is not the one that waits.

Pure except for `wait`: the decisions are the part a test can hold.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

# The ceiling on frames a second, before a whole number of refreshes is taken.
# High enough that a 120 Hz panel gets every refresh; low enough that a 165 Hz
# one gets every second rather than a frame budget of six milliseconds, which
# the grid meets and a busy frame occasionally does not -- and a missed
# refresh is exactly the uneven step this module exists to remove.
MOST = 125.0

# How long nothing has to happen before the screen counts as idle, and how
# often it is redrawn then. Redrawn rather than frozen: anything that changes
# without an event -- a cover arriving, the strip's status -- is still on
# screen within a tenth of a second, so nothing can go stale for good because
# it forgot to say it was busy.
IDLE_AFTER = 1.0
IDLE_RATE = 10.0

# How many frames the verdict on vsync is taken over. Presented back to back
# with no sleep: a panel that is waiting makes them a refresh apart, and one
# that is not makes them as short as the drawing.
PROBE = 20

# The fraction of a refresh the average probe frame has to reach to count as
# waited for. Well under one: a compositor's timing wobbles, and a driver that
# does not wait at all produces frames a tenth of a refresh long.
HOLDS_AT = 0.6


def every(refresh: float, most: float = MOST) -> int:
    """How many of the panel's refreshes one frame spans.

    The smallest whole number that keeps the rate at or under `most`. Whole,
    because a frame shown for three refreshes and then two is the stutter.
    """
    if refresh <= 0 or most <= 0:
        return 1
    return max(1, math.ceil(refresh / most - 1e-9))


@dataclass
class Pace:
    """The pacing for one window, told what happened and asked how long to wait."""

    refresh: float = 60.0
    vsync: bool = True
    most: float = MOST
    idle_after: float = IDLE_AFTER
    idle_rate: float = IDLE_RATE
    # Whether presenting has been seen to wait for the panel. None while the
    # probe is still running.
    holds: bool | None = None
    _probe: deque = field(default_factory=lambda: deque(maxlen=PROBE))
    _last_present: float | None = None
    # When the next unwaited frame is due to start. A schedule rather than a
    # measurement: the first version slept "a frame minus the last interval",
    # which on a panel that does not wait alternated 2.6 ms and 16.7 ms frames
    # -- 103 fps in a real trace, and as uneven as the stutter it replaced.
    _due: float | None = None
    _last_busy: float = 0.0

    @property
    def period(self) -> float:
        return 1.0 / self.refresh if self.refresh > 0 else 1.0 / 60.0

    def presented(self, now: float) -> None:
        """A present has just returned. Feeds the probe while it runs."""
        if self._last_present is not None and self.holds is None and self.vsync:
            self._probe.append(now - self._last_present)
            if len(self._probe) >= PROBE:
                typical = statistics.median(self._probe)
                self.holds = typical >= HOLDS_AT * self.period
                if self.holds and 20.0 <= 1.0 / typical <= 500.0:
                    # The panel's own answer, which beats the one asked of the
                    # desktop: that names a monitor, and with three of them
                    # the window is not always on the one it names.
                    self.refresh = 1.0 / typical
        self._last_present = now
        if not self.vsync:
            self.holds = False

    def moved(self, refresh: float) -> None:
        """The window is on a panel with this rate now.

        Asked of SDL for the monitor the window is actually on -- a desk with
        60, 165 and 165 Hz panels was paced for the first one's 60 wherever
        the window was. Ignored once the probe has measured the panel itself:
        that answer is the panel's own.
        """
        if refresh > 0 and not self.holds and refresh != self.refresh:
            self.refresh = float(refresh)
            self._due = None

    def busy(self, now: float) -> None:
        """Something is moving, or somebody did something."""
        self._last_busy = now

    def idle(self, now: float) -> bool:
        """Whether nothing has happened for long enough to stop drawing fast."""
        return now - self._last_busy >= self.idle_after

    def probing(self) -> bool:
        return self.vsync and self.holds is None

    def rest(self, now: float, cap: float | None = None) -> float:
        """Seconds to sleep before starting the next frame, from `now`, which
        is when the last present returned.

        While probing, none: the probe is frames presented back to back.
        Waited for, `every - 1` refreshes, so the next present lands exactly
        `every` refreshes after this one. Not waited for, whatever is left of
        the frame at the capped rate. Idle, a tenth of a second, which `wait`
        cuts short the moment anything arrives. `cap` is a caller's own
        ceiling (the install log draws at 30).
        """
        # The probe first: a window that opens with nothing moving would
        # otherwise take its idle tenth of a second between probe frames and
        # conclude the panel was waiting for it.
        if self.probing():
            return 0.0
        if self.idle(now):
            return 1.0 / self.idle_rate
        most = min(self.most, cap) if cap else self.most
        if self.holds:
            return (every(self.refresh, most) - 1) * self.period
        # Timed by this program, at a whole number of the panel's refreshes
        # all the same: 82.5 on 165 Hz rather than 60, which is 2.75 of them
        # and lands unevenly however well it is timed.
        frame = every(self.refresh, most) * self.period
        if self._due is None or now - self._due > frame:
            # First frame, or fell behind (a slow frame, a sleep): start the
            # schedule again from here rather than rushing to catch up.
            self._due = now
        self._due += frame
        return max(0.0, self._due - now)


def wait(seconds: float, woken=None, *, peek=None, sleep=time.sleep, clock=time.monotonic) -> None:
    """Sleep `seconds`, or less if something arrives.

    Long waits are sliced so an SDL event (`peek`, pygame's own
    `event.peek`) or anything `woken` reports -- padmap's socket having
    something to say -- ends it within a few milliseconds. Short ones, the
    refresh-aligned rests, are one sleep: slicing them would only add the
    slices' own lateness to a frame that has to land on time.
    """
    if seconds <= 0:
        return
    if seconds < 0.02 or (peek is None and woken is None):
        sleep(seconds)
        return
    end = clock() + seconds
    while True:
        if peek is not None and peek():
            return
        if woken is not None and woken():
            return
        left = end - clock()
        if left <= 0:
            return
        sleep(min(0.004, left))
