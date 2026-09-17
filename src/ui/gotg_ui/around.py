"""Moving around a controller with a d-pad.

The binding screen draws a pad with a circle on every button, and the question
is which button "right" means when you are on the d-pad. A list would answer it
by file order, which on a controller is no order at all: pressing down would
jump from the left stick to a shoulder because that is how the config happens
to read.

So it is answered geometrically, from the anchors the drawing already carries.
Everything in the half-plane you pressed towards is a candidate, and the one
that is most nearly straight ahead wins, with distance breaking the tie. That
is the same rule a television remote uses, and it is the one that makes "right,
right, right" walk the face buttons rather than wander.

Pure: it takes positions and gives back a name. No pygame, no artwork.
"""

from __future__ import annotations

import math

# How much being off to the side counts against a candidate.
#
# At 1.0 a button 45 degrees off is worth the same as one twice as far away
# straight ahead, which in practice picks diagonals too eagerly on a pad where
# the face buttons sit in a diamond. Two is firm enough to keep a press
# travelling in the direction it was pressed.
SIDEWAYS_PENALTY = 2.0


def nearest(anchors: dict[str, tuple[float, float]], current: str, step: tuple[int, int]) -> str:
    """The control one press of `step` from `current`, or `current` itself.

    Staying put rather than wrapping: a pad is a picture of a physical object,
    and pressing right at the right-hand edge of one should do nothing, the way
    it does on the thing itself.
    """
    here = anchors.get(current)
    dx, dy = step
    if here is None or (dx == 0 and dy == 0):
        return current

    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length

    best, best_score = current, None
    for name, (x, y) in anchors.items():
        if name == current:
            continue
        ox, oy = x - here[0], y - here[1]
        along = ox * ux + oy * uy
        if along <= 0:
            continue  # behind, or exactly sideways: not in this direction
        across = abs(ox * -uy + oy * ux)
        score = along + SIDEWAYS_PENALTY * across
        if best_score is None or score < best_score:
            best, best_score = name, score
    return best


def first(anchors: dict[str, tuple[float, float]]) -> str:
    """Where the cursor starts: the topmost, then leftmost, control.

    Somewhere definite, and the same one every time. Reading order on a
    picture, which is where an eye lands anyway.
    """
    if not anchors:
        return ""
    return min(anchors.items(), key=lambda item: (round(item[1][1], 3), item[1][0]))[0]
