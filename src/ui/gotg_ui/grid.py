"""Selection and paging over a Library — the cursor, without the screen.

Its own module rather than a class inside app.py, and that is the whole point:
the awkward cases are the ones a display cannot show you. A short last page, a
filter that emptied the grid, a stick held against the right-hand column. All
of them reachable from a test because nothing here imports pygame.
"""

from __future__ import annotations

from .catalog import Game, Library
from .layout import COLUMNS, ROWS


class Grid:
    """Where the cursor is, and which page it is on."""

    def __init__(self, library: Library):
        self.library = library
        self.page_index = 0
        self.selected = 0

    @property
    def page(self):
        return self.library.page(self.page_index)

    def move(self, dx: int, dy: int) -> None:
        """Step the cursor, carrying between pages at the edges.

        Carrying rather than stopping: paging is the shoulder buttons' job, and
        a stick that dead-ends at the right-hand column makes somebody press
        them for something the stick should have done.
        """
        page = self.page
        if not page:
            return
        column, row = self.selected % COLUMNS, self.selected // COLUMNS
        if dy:
            row = max(0, min(ROWS - 1, row + dy))
        if dx:
            column += dx
            if column < 0:
                if self.turn(-1):
                    column = COLUMNS - 1
                else:
                    column = 0
            elif column >= COLUMNS:
                if self.turn(1):
                    column = 0
                else:
                    column = COLUMNS - 1
        self.selected = min(row * COLUMNS + column, len(self.page) - 1)

    def select(self, index: int) -> bool:
        """Put the cursor on one tile of this page. False if there is no game
        there — the pointer is over a gap, or past the end of a short page."""
        page = self.page
        if not 0 <= index < len(page):
            return False
        self.selected = index
        return True

    def turn(self, delta: int) -> bool:
        """A page forward or back. False when there is nowhere to go."""
        target = self.page_index + delta
        if not 0 <= target < self.library.pages:
            return False
        self.page_index = target
        self.selected = min(self.selected, len(self.page) - 1)
        return True

    @property
    def game(self) -> Game | None:
        page = self.page
        return page[self.selected] if page and self.selected < len(page) else None
