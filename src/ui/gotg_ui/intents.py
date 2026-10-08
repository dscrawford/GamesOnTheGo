"""What a key or a pad press means to a screen: Back, OK, a step, or the space bar.

`run()` read "Escape or b, or the pad's B, is back; Return or keypad Enter, or
the pad's A, is confirm" eight times -- the loader, the saves check, the
choice, the Saves screen, storage, the menu, the filter panel -- each with the
key list written out again and each ordering key and pad differently. Where
they differ is deliberate and stays visible: Space confirms in the menu and the
filter panel and nowhere else (so it is its own word here, and a screen opts in
with `confirms(space=True)`); typing and the grid have keys of their own and do
not ask this at all.

No pygame: the caller resolves the event to the three things this needs -- the
key of a KEYDOWN, the name of the pad button, the pad's direction -- and the
last two are already None for a pad danstick has not published (`pads.py`), so
an unseated pad is `NONE` here without this having to know about seats. The
keycodes are SDL's, written down so this stays testable; `app.py` asserts them
against pygame at import, the way `pads.py` does for the button numbering.

`keyboard_heard` is the other half of the keyboard rule (`keys.py`): until the
keyboard has a seat the only key heard anywhere is the space bar that asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .buttons import A, B


class Key(IntEnum):
    """The SDL keycodes the screens read."""

    BACKSPACE = 8
    ESCAPE = 27
    B = 98
    RETURN = 13
    SPACE = 32
    KP_ENTER = 1073741912
    RIGHT = 1073741903
    LEFT = 1073741904
    DOWN = 1073741905
    UP = 1073741906


_ARROWS = {
    Key.UP: (0, -1),
    Key.DOWN: (0, 1),
    Key.LEFT: (-1, 0),
    Key.RIGHT: (1, 0),
}


@dataclass(frozen=True)
class Intent:
    """One meaning. `kind` is "back", "ok", "move", "space" or "none"."""

    kind: str
    step: tuple[int, int] = (0, 0)

    @property
    def dx(self) -> int:
        return self.step[0]

    @property
    def dy(self) -> int:
        return self.step[1]

    def confirms(self, space: bool = False) -> bool:
        """Whether this confirms: OK always, and the space bar on the screens
        that let it."""
        return self.kind == "ok" or (space and self.kind == "space")


BACK = Intent("back")
OK = Intent("ok")
SPACE = Intent("space")
NONE = Intent("none")


def MOVE(step: tuple[int, int]) -> Intent:  # noqa: N802 -- reads as the others do
    return Intent("move", (step[0], step[1]))


def intent(key: int | None = None, button: str | None = None, step: tuple[int, int] | None = None) -> Intent:
    """The meaning of one event: `key` for a key going down, else the pad's
    `button` name and `step` direction (both None for anything unseated).

    A direction wins over a button, as it did where the branches were written
    by hand; a d-pad press is never also A or B.
    """
    if key is not None:
        if key in (Key.ESCAPE, Key.B):
            return BACK
        if key in (Key.RETURN, Key.KP_ENTER):
            return OK
        if key == Key.SPACE:
            return SPACE
        if key in _ARROWS:
            return MOVE(_ARROWS[key])
        return NONE
    if step:
        return MOVE(step)
    if button == B:
        return BACK
    if button == A:
        return OK
    return NONE


def keyboard_heard(key: int | None, drives: bool) -> bool:
    """Whether a keyboard event is read at all.

    `drives` is `keys.drives(...)`: whether danstick has seated the keyboard.
    Without it the space bar is still heard, because a keyboard that cannot
    ask for a seat cannot be given one -- and nothing else is.
    """
    return key == Key.SPACE or drives
