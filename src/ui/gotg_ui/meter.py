"""Where a frame's time went, for the times somebody says it feels slow.

"The UI runs at pretty low FPS" is a report nobody can act on, because the two
halves of a frame fail for different reasons and are fixed in different
places. Drawing is this program: too many surfaces, a scale nobody cached, a
file read per icon. Presenting is SDL and the machine underneath it: a
fullscreen `SCALED` surface being resampled to a 4K panel in software costs
tens of milliseconds and no amount of tidying here touches it.

So both are counted, separately, and said once a second -- with
`GOTG_UI_FPS=1` on the terminal, and always into the trace when one is
running. Pure arithmetic, no clock of its own: the loop passes the times it
already has.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def wanted() -> bool:
    """Whether to say it out loud. The trace gets it either way."""
    return os.environ.get("GOTG_UI_FPS") == "1"


@dataclass
class Meter:
    """Frame times, averaged over a second."""

    every: float = 1.0
    frames: int = 0
    drawing: float = 0.0
    presenting: float = 0.0
    waiting: float = 0.0
    worst: float = 0.0
    since: float | None = None

    def frame(self, drawing: float, presenting: float, waiting: float, now: float) -> dict | None:
        """One frame's three parts, in seconds. A summary once a second.

        `waiting` is the sleep the frame cap does -- time the program chose
        not to use. A frame that is slow *and* waiting is slow for a reason
        nobody here can fix by drawing less.
        """
        if self.since is None:
            self.since = now
        self.frames += 1
        self.drawing += drawing
        self.presenting += presenting
        self.waiting += waiting
        self.worst = max(self.worst, drawing + presenting)
        if now - self.since < self.every:
            return None
        spent = now - self.since
        said = {
            "fps": round(self.frames / spent, 1),
            "draw_ms": round(1000 * self.drawing / self.frames, 2),
            "present_ms": round(1000 * self.presenting / self.frames, 2),
            "idle_ms": round(1000 * self.waiting / self.frames, 2),
            "worst_ms": round(1000 * self.worst, 2),
        }
        self.frames = 0
        self.drawing = self.presenting = self.waiting = self.worst = 0.0
        self.since = now
        return said
