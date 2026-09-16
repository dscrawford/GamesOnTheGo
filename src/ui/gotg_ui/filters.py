"""The filter panel: narrowing the library from a controller.

Everything here was already in `Browser` and only half of it could be reached
without a keyboard. A pad could walk platforms forwards — twelve of them, one
way round — and could not touch regions at all, because they were on shift-Tab.

Start opens it. Up and down choose what to narrow by; pressing one opens the
list of what it can be, because twelve platforms is a list to look down rather
than a value to press right eleven times. Left and right still nudge, for when
the answer is next door.

The values are read back out of the browser rather than kept here: the panel is
a view of the filters, not a second copy of them, so a search typed on the grid
is already correct when the panel opens.

Model only, and pygame-free. What it looks like is drawing's business.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .browser import ALL as PRESENCE_ALL
from .browser import INSTALLED as PRESENCE_INSTALLED
from .browser import MISSING as PRESENCE_MISSING

PLATFORM = "platform"
REGION = "region"
INSTALLED = "installed"
SEARCH = "search"
CLEAR = "clear"

# In the order somebody reaches for them: what console, then where it is from,
# then whether it is on this machine. Search last because typing is the slow
# one, and clear at the bottom because it undoes the four above it.
ROWS = (PLATFORM, REGION, INSTALLED, SEARCH, CLEAR)

LABELS = {
    PLATFORM: "Platform",
    REGION: "Region",
    INSTALLED: "Installed",
    SEARCH: "Search",
    CLEAR: "Clear all",
}

# What a row does when it is pressed rather than nudged.
TYPING = "typing"

# What the "Installed" row can be. Three answers rather than two: a yes/no
# toggle can ask for what is downloaded and cannot ask for what is not, which
# is the question somebody browsing for something new is asking.
INSTALLED_OPTIONS = (PRESENCE_ALL, PRESENCE_INSTALLED, PRESENCE_MISSING)


@dataclass
class Choice:
    """An open dropdown: what it is for, what it offers, where the cursor is."""

    row: str
    options: tuple[str, ...]
    index: int = 0

    @property
    def value(self) -> str:
        return self.options[self.index]

    def move(self, delta: int) -> None:
        """Wrapping, like the rows: a dozen platforms is a long way back up."""
        self.index = (self.index + delta) % len(self.options)


@dataclass
class Filters:
    """Which row the cursor is on, and the list open over it if there is one.

    The values live in the browser.
    """

    index: int = 0
    rows: tuple[str, ...] = field(default=ROWS)
    choice: Choice | None = None

    @property
    def row(self) -> str:
        return self.rows[self.index]

    def move(self, delta: int) -> None:
        """Up and down, wrapping. Five rows on a handheld: wrapping is one
        press from the bottom back to the top rather than four."""
        self.index = (self.index + delta) % len(self.rows)

    def adjust(self, browser, delta: int) -> None:
        """Left or right on the current row.

        A toggle and a cycle both answer to the same two directions, which is
        what lets the whole panel be driven by a d-pad and nothing else.
        """
        row = self.row
        if row == PLATFORM:
            browser.cycle_platform(delta)
        elif row == REGION:
            browser.cycle_region(delta)
        elif row == INSTALLED:
            # Round the three, both ways, like every other row here.
            options = INSTALLED_OPTIONS
            index = options.index(value_of(browser, INSTALLED))
            browser.set_presence(options[(index + delta) % len(options)])

    def press(self, browser) -> str | None:
        """A or Enter on the current row.

        Opens the row's list of choices, or does the row's one thing. Returns
        TYPING when the row wants the keyboard -- the caller owns the text box,
        and on a Deck the Steam keyboard rises over it.
        """
        row = self.row
        if row == SEARCH:
            return TYPING
        if row == CLEAR:
            clear(browser)
            return None
        options = options_for(browser, row)
        if not options:
            return None
        # Opened on what it already is, so the list starts where the eye is.
        current = value_of(browser, row)
        self.choice = Choice(
            row=row,
            options=options,
            index=options.index(current) if current in options else 0,
        )
        return None

    def choose(self, browser) -> None:
        """A on an open list: take the highlighted option and close."""
        if self.choice is None:
            return
        set_value(browser, self.choice.row, self.choice.value)
        self.choice = None

    def close(self) -> None:
        """B on an open list. Closes the list, not the panel -- one press, one
        step back, which is what B means everywhere else here."""
        self.choice = None

    @property
    def open(self) -> bool:
        return self.choice is not None


def clear(browser) -> None:
    """Back to the whole library.

    Every filter at once, because that is the thing somebody wants when they
    have narrowed to nothing and cannot see what did it.
    """
    browser.set_platform(browser.platforms[0])
    browser.set_region(browser.regions[0])
    browser.set_search("")
    browser.set_presence(PRESENCE_ALL)


def options_for(browser, row: str) -> tuple[str, ...]:
    """Everything this row can be, in the order the list shows them.

    "all" is first in both lists rather than sorted into them, so the way back
    to the whole library is the top of the list rather than somewhere in it.
    """
    if row == PLATFORM:
        return tuple(browser.platforms)
    if row == REGION:
        return tuple(browser.regions)
    if row == INSTALLED:
        return INSTALLED_OPTIONS
    return ()


def set_value(browser, row: str, value: str) -> None:
    """Put one row where the list says. The only writer besides `clear`."""
    if row == PLATFORM:
        browser.set_platform(value)
    elif row == REGION:
        browser.set_region(value)
    elif row == INSTALLED:
        browser.set_presence(value)


def value_of(browser, row: str) -> str:
    """What the row currently says, in words rather than state."""
    if row == PLATFORM:
        return browser.platform
    if row == REGION:
        return browser.region
    if row == INSTALLED:
        return browser.presence
    if row == SEARCH:
        return browser.search or "—"
    return ""


def rows_for(browser, panel: Filters) -> list[tuple[str, str, bool]]:
    """(label, value, selected) for every row, which is the whole drawing."""
    return [
        (LABELS[row], value_of(browser, row), index == panel.index)
        for index, row in enumerate(panel.rows)
    ]
