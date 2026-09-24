"""A held direction, from a d-pad or a stick, as a stream of steps.

The grid moved one tile per d-pad press and not at all for the stick: reaching
the far end of a library meant tapping all the way there, and a stick pushed
did nothing, which on a Deck -- where the stick is under the thumb and the
d-pad is not -- read as a picker that had stopped answering. A direction now
steps once as it arrives, waits DELAY, then repeats every INTERVAL for as long
as it is held, the way every console's menus behave.

A pure model, as the screens are: the loop feeds it events and the time, and
posts the steps it returns as d-pad presses, so every screen that already
answers the d-pad answers the stick and the repeat without knowing either
exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# How long a direction is held before it repeats, and then how often. Long
# enough that a tap is one step, short enough that crossing a page of tiles
# does not become a wait.
DELAY = 0.35
INTERVAL = 0.1

# A stick counts as pushed past ENTER and as let go below EXIT. Two numbers,
# not one: a thumb resting at the threshold stepped over and over as the
# reading wobbled either side of it.
ENTER = 0.5
EXIT = 0.35

Direction = tuple[int, int]


@dataclass(frozen=True)
class _Held:
    direction: Direction
    next_at: float


@dataclass(frozen=True)
class _Stick:
    x: float = 0.0
    y: float = 0.0


def _pushed(stick: _Stick) -> Direction | None:
    """The way a stick points, by its stronger axis, if it is past ENTER."""
    if max(abs(stick.x), abs(stick.y)) < ENTER:
        return None
    if abs(stick.x) >= abs(stick.y):
        return (1 if stick.x > 0 else -1, 0)
    return (0, 1 if stick.y > 0 else -1)


def _still_pushed(stick: _Stick, direction: Direction) -> bool:
    """Whether the stick is still that way, by the lower EXIT threshold."""
    along = stick.x * direction[0] + stick.y * direction[1]
    return along >= EXIT


@dataclass(frozen=True)
class Nav:
    """Every held direction, by pad (SDL instance id) and source."""

    sticks: dict[int, _Stick] = field(default_factory=dict)
    held: dict[tuple[int, str], _Held] = field(default_factory=dict)

    def _with(self, key: tuple[int, str], held: _Held | None) -> dict[tuple[int, str], _Held]:
        out = {k: v for k, v in self.held.items() if k != key}
        if held is not None:
            out[key] = held
        return out

    def stick(self, pad: int, axis: int, value: float, now: float) -> tuple[Nav, Direction | None]:
        """A left-stick reading (axis 0 across, 1 down, -1..1). Returns the
        step to take now, if this reading is a push in a new direction."""
        if axis not in (0, 1):
            return self, None
        before = self.sticks.get(pad, _Stick())
        stick = replace(before, x=value) if axis == 0 else replace(before, y=value)
        sticks = {**self.sticks, pad: stick}
        key = (pad, "stick")
        holding = self.held.get(key)
        pushed = _pushed(stick)
        if holding is not None and (pushed is None or pushed == holding.direction):
            if _still_pushed(stick, holding.direction):
                return replace(self, sticks=sticks), None
            return replace(self, sticks=sticks, held=self._with(key, None)), None
        if pushed is None:
            return replace(self, sticks=sticks), None
        return replace(self, sticks=sticks, held=self._with(key, _Held(pushed, now + DELAY))), pushed

    def press(self, pad: int, direction: Direction, now: float) -> Nav:
        """A d-pad direction went down. Its first step is the press itself,
        which the screen has already taken; this only arranges the repeat."""
        return replace(self, held=self._with((pad, "pad"), _Held(direction, now + DELAY)))

    def release(self, pad: int, direction: Direction) -> Nav:
        """A d-pad direction came up; (0, 0), a hat centring, is any."""
        held = self.held.get((pad, "pad"))
        if held is None or direction not in ((0, 0), held.direction):
            return self
        return replace(self, held=self._with((pad, "pad"), None))

    def forget(self, pad: int) -> Nav:
        """A pad went away: nothing of it is held any more."""
        return Nav(
            sticks={k: v for k, v in self.sticks.items() if k != pad},
            held={k: v for k, v in self.held.items() if k[0] != pad},
        )

    def due(self, now: float) -> tuple[Nav, list[tuple[int, Direction]]]:
        """The repeats that are due, one per held direction at most: a frame
        that came late does not bunch several into a jump."""
        steps = []
        held = {}
        for key, entry in self.held.items():
            if now + 1e-9 >= entry.next_at:
                steps.append((key[0], entry.direction))
                entry = replace(entry, next_at=now + INTERVAL)
            held[key] = entry
        return replace(self, held=held), steps

    def holding(self) -> bool:
        """Whether anything is held: the loop keeps its frame rate while so."""
        return bool(self.held)
