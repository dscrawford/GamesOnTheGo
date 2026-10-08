"""The "Update available" chip's state, as a value.

Pulled out of `run()`, which kept three locals for it -- the loop's own word
(`chip_phase`), when that word gives way (`chip_until`), and where the chip was
last drawn (`chip_rect`) -- and spelled the timing at every call of `say_chip`.
The seam is that the chip is the report's words except for a while after a
press: `words()` is that rule, `say()` starts a while, and the rect is kept
here only because a click is matched against it. No pygame (the rect is the
layout Tile `draw_chip` returns); frozen, so every press is a new value.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import updates

# How long each of the loop's words outlasts the report's. An update in flight
# holds its word for as long as it can plausibly run; the rest are results.
DEFAULT_SECONDS = 6.0
SECONDS = {"updating": 3600.0, "up-to-date": 10.0, "updated-here": 10.0, "failed": 10.0}


@dataclass(frozen=True)
class ChipState:
    phase: str | None = None
    until: float = 0.0
    rect: object | None = None

    def say(self, phase: str | None, now: float, seconds: float | None = None) -> ChipState:
        """The loop's word, from `now` for `seconds` (the phase's own length
        when not given)."""
        if seconds is None:
            seconds = SECONDS.get(phase, DEFAULT_SECONDS)
        return replace(self, phase=phase, until=now + seconds)

    def live(self, now: float) -> str | None:
        """The loop's word while it lasts, else None: the report speaks."""
        return self.phase if now < self.until else None

    def words(self, report: updates.Report | None, running_root: str | None, now: float) -> str | None:
        return updates.chip(report, running_root, self.live(now))

    def updating(self, now: float) -> bool:
        """An update is in flight: a second press would queue behind it."""
        return self.live(now) == "updating"

    def drawn(self, tile: object | None) -> ChipState:
        """Where the chip was drawn this frame (a Tile with x, y, width,
        height)."""
        return replace(self, rect=tile)

    def hit(self, pos: tuple[int, int]) -> bool:
        tile = self.rect
        if tile is None:
            return False
        x, y = pos
        return tile.x <= x < tile.x + tile.width and tile.y <= y < tile.y + tile.height
