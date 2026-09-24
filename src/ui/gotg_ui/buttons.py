"""Which button a pad event is, as a name rather than a number.

Every screen here used to compare `event.button` to a number, and the numbers
were an Xbox pad's: 4 and 5 for the shoulders, 7 for Start. That is the raw
joystick API, where an index means whatever the hardware says. On a Steam
Controller the shoulders are 9 and 10 and Start is 6 — so the bumpers turned no
pages and Start opened nothing, while A and B happened to line up and hid it.

SDL already solves this: its *game controller* API maps every pad it recognises
onto one layout. The numbers below are that layout's, and they are written out
rather than imported so this module holds no pygame and can be tested without a
screen. `pads.py` is the half that talks to SDL, and asserts they still agree.
"""

from __future__ import annotations

A = "a"
B = "b"
X = "x"
Y = "y"
BACK = "back"
START = "start"
LB = "lb"
RB = "rb"

UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"

# SDL's game controller layout, which is the same on every pad it knows.
STANDARD = {
    0: A, 1: B, 2: X, 3: Y,
    4: BACK, 6: START,
    9: LB, 10: RB,
    11: UP, 12: DOWN, 13: LEFT, 14: RIGHT,
}

# What a pad SDL has never heard of probably means: an Xbox pad's raw order,
# which is what most hardware copies and what this code assumed of everything.
RAW = {0: A, 1: B, 2: X, 3: Y, 4: LB, 5: RB, 6: BACK, 7: START}

# A direction as a step on the grid, y down, which is how every screen counts.
STEPS = {UP: (0, -1), DOWN: (0, 1), LEFT: (-1, 0), RIGHT: (1, 0)}


def name_for(index: int, standard: bool) -> str | None:
    """The button at this index, on a pad SDL maps or one it does not."""
    return (STANDARD if standard else RAW).get(index)


def step_for(name: str | None) -> tuple[int, int] | None:
    """Which way a named direction points, or None for anything else."""
    return STEPS.get(name) if name else None


def hat_step(value: tuple[int, int]) -> tuple[int, int] | None:
    """A hat's own reading, in the grid's direction.

    SDL's hat is y-up and every screen here is y-down. The centre -- both axes
    zero, which is the release -- is not a direction: stepping on it would move
    twice for one press.
    """
    dx, dy = value
    return (dx, -dy) if (dx or dy) else None


def cardinal(step: tuple[int, int] | None) -> tuple[int, int] | None:
    """The step if it is one of the four the d-pad has buttons for.

    A hat can be held on a diagonal, and a diagonal repeated as a d-pad press
    looked up a button that does not exist -- the picker exited. It steps
    nowhere instead, as the grid already treats a diagonal.
    """
    return step if step in STEPS.values() else None
