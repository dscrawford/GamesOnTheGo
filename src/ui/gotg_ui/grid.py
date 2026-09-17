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

    def __init__(self, library: Library, columns: int = COLUMNS, rows: int = ROWS):
        """The shape is the cursor's, not the layout module's.

        Two views draw the same library in different shapes -- ten big tiles,
        or rows of small icons -- and a cursor that stepped by the grid's five
        columns inside a shelf eight wide would skip three games per press.
        """
        self.library = library
        self.columns = max(1, columns)
        self.rows = max(1, rows)
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
        column, row = self.selected % self.columns, self.selected // self.columns
        if dy and self.columns == 1:
            # A list is one sequence, so down from the last line carries to the
            # next page the way a scroll does. In a grid it clamps instead: the
            # row below the bottom one is nothing, and paging there would move
            # the cursor two places for one press.
            row += dy
            if row < 0:
                row = self.rows - 1 if self.turn(-1) else 0
            elif row >= self.rows or row >= len(self.page):
                row = 0 if self.turn(1) else min(row, len(self.page) - 1)
        elif dy:
            row = max(0, min(self.rows - 1, row + dy))
        if dx:
            column += dx
            if column < 0:
                if self.turn(-1):
                    column = self.columns - 1
                else:
                    column = 0
            elif column >= self.columns:
                if self.turn(1):
                    column = 0
                else:
                    column = self.columns - 1
        self.selected = min(row * self.columns + column, len(self.page) - 1)

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
