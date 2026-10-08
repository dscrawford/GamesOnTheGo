"""What one frame of the grid or the shelf shows, and the words on it.

`draw`, `draw_shelf` and `draw_row` took the same nine to eleven positional
arguments -- the cursor, the art, the status line, which games are installed,
which are installing, which are behind, the chip -- and `run()` spelled them
out three times in the same order, where swapping two sets of the same type
would draw without complaint. They are one value now, built once a frame.

The text drawn from them is here too, because it is the part of drawing that
can be wrong and still be tested: no pygame in this module. The draw functions
keep only the surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Game
from .grid import Grid

Ring = tuple[float | None, bool]
Keys = set[tuple[str, str]] | frozenset[tuple[str, str]]


@dataclass(frozen=True)
class GridView:
    """The state of the screen under the grid or the shelf, for one frame.

    `art` is the picker's cover store (`.get(key)`), `rings` the installs in
    flight by game key as (fraction or None, failed), `chip` the words of the
    "Update available" chip. Every one is optional, as it was in the
    signatures: nothing installed, nothing installing, no chip.
    """

    state: Grid
    art: object | None = None
    status: str = ""
    installed: Keys | None = None
    rings: dict[tuple[str, str], Ring] | None = None
    outdated: Keys | None = None
    chip: str | None = None


def row_under_text(game: Game, ring: Ring | None, installed: bool, outdated: bool) -> str:
    """The line under a shelf row's title: the platform, and what is going on.

    An install in flight outranks the rest, since the ring is drawn beside it;
    an update is only worth saying about a game that is here.
    """
    if ring is not None:
        return game.platform + ("   ·   install failed" if ring[1] else "   ·   installing")
    if installed and outdated:
        return game.platform + "   ·   installed  ·  update available"
    return game.platform + ("   ·   installed" if installed else "")


def status_line(state: Grid, status: str, typing: str | None) -> tuple[str, bool]:
    """The corner's words, and whether they are bright (being typed).

    The browser's status when it has one, else which page this is. While
    typing, the search box replaces it: it is the thing being edited, and two
    lines competing for the same corner reads as neither.
    """
    if typing is not None:
        return f"search: {typing}_", True
    if status:
        return status, False
    if state.library.pages:
        return f"page {state.page_index + 1} of {state.library.pages}  ·  {len(state.library)} games", False
    return "no games in the catalog", False
