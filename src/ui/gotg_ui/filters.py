"""The filter panel: narrowing the library from a controller.

Everything here was already in `Browser` and only half of it could be reached
without a keyboard. A pad could walk platforms forwards — twelve of them, one
way round — and could not touch regions at all, because they were on shift-Tab.

So this is a list rather than more buttons. Up and down choose what to narrow
by, left and right change it, and both directions exist. The value shown is
read back out of the browser rather than kept here: the panel is a view of the
filters, not a second copy of them, so a search typed on the grid is already
correct when the panel opens.

Model only, and pygame-free. What it looks like is drawing's business.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    INSTALLED: "Installed only",
    SEARCH: "Search",
    CLEAR: "Clear all",
}

# What a row does when it is pressed rather than nudged.
TYPING = "typing"


@dataclass
class Filters:
    """Which row the cursor is on. The values live in the browser."""

    index: int = 0
    rows: tuple[str, ...] = field(default=ROWS)

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
            browser.toggle_installed()

    def press(self, browser) -> str | None:
        """A or Enter on the current row.

        Returns TYPING when the row wants the keyboard -- the caller owns the
        text box, and on a Deck the Steam keyboard rises over it.
        """
        row = self.row
        if row == SEARCH:
            return TYPING
        if row == CLEAR:
            clear(browser)
            return None
        # Everything else is a nudge, and pressing it means the same as right.
        self.adjust(browser, 1)
        return None


def clear(browser) -> None:
    """Back to the whole library.

    Every filter at once, because that is the thing somebody wants when they
    have narrowed to nothing and cannot see what did it.
    """
    browser.set_platform(browser.platforms[0])
    browser.set_region(browser.regions[0])
    browser.set_search("")
    if browser.installed_only:
        browser.toggle_installed()


def value_of(browser, row: str) -> str:
    """What the row currently says, in words rather than state."""
    if row == PLATFORM:
        return browser.platform
    if row == REGION:
        return browser.region
    if row == INSTALLED:
        return "yes" if browser.installed_only else "no"
    if row == SEARCH:
        return browser.search or "—"
    return ""


def rows_for(browser, panel: Filters) -> list[tuple[str, str, bool]]:
    """(label, value, selected) for every row, which is the whole drawing."""
    return [
        (LABELS[row], value_of(browser, row), index == panel.index)
        for index, row in enumerate(panel.rows)
    ]
