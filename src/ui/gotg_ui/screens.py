"""Which screen owns the input, and what a press on the grid means.

Pulled out of `run()`, which answered "who has the input?" twice: a chain of
eight `if`s in the event loop and another of seven in the drawing, each with
its own order. They agreed only by being written next to each other. The seam
is the question itself -- it depends on nothing but which pieces of state are
open -- so `screen_of` answers it once and `run()` dispatches on the answer for
both reading and drawing.

The order is the input chain's: the loader, the saves check and choice, the
saves list, storage, the menu, the filter panel, typing, the grid. The panel
with a search being typed into is its own screen: its keys are text (the
typing screen's), but it stays drawn over the grid, as it always was.
"""

from __future__ import annotations

from enum import Enum

from . import buttons


class Screen(Enum):
    PREPARE = "prepare"
    SAVES_CHECK = "saves-check"
    SAVES = "saves"
    STORAGE = "storage"
    MENU = "menu"
    PANEL = "panel"
    PANEL_TYPING = "panel-typing"
    TYPING = "typing"
    GRID = "grid"


def screen_of(
    *,
    preparing: bool,
    saves_check: bool,
    saves: bool,
    storage: bool,
    menu: bool,
    panel: bool,
    typing: bool,
) -> Screen:
    """The screen that owns input now. `saves_check` is a check in flight or
    a choice between two saves on screen."""
    if preparing:
        return Screen.PREPARE
    if saves_check:
        return Screen.SAVES_CHECK
    if saves:
        return Screen.SAVES
    if storage:
        return Screen.STORAGE
    if menu:
        return Screen.MENU
    if panel:
        return Screen.PANEL_TYPING if typing else Screen.PANEL
    if typing:
        return Screen.TYPING
    return Screen.GRID


# By name, not by index: the shoulders are 4 and 5 on an Xbox pad and 9 and 10
# on a Steam Controller (buttons.py).
_GRID_PRESSES = {
    buttons.A: "menu",
    buttons.B: "quit",
    buttons.LB: "page-back",
    buttons.RB: "page-forward",
    # The next platform: quicker than the panel when it is the next one wanted.
    buttons.Y: "platform",
    # Search. A Deck raises the Steam keyboard over this.
    buttons.X: "search",
    # Start is the button somebody presses looking for options, so it opens the
    # filter panel, which is where everything that narrows the library lives.
    buttons.START: "panel",
    buttons.BACK: "update",
}


def grid_press(button: str | None) -> str | None:
    """What a pad button does on the grid, or None for one it does not use."""
    return _GRID_PRESSES.get(button)
