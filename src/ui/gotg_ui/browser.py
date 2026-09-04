"""What is currently being looked at: a platform, a search, and a cursor.

The grid is ten tiles and the library is 568 pages of them, so this is the
part that makes it a picker rather than something to scroll past. It holds no
pygame, because the case worth testing is not what it looks like — it is what
happens to the cursor when the ground moves under it.
"""

from __future__ import annotations

from .catalog import Game, Library
from .grid import Grid

# Pinned to the front of the list rather than sorted into it, so one press
# from the starting position widens to everything rather than narrowing to
# whichever platform happens to sort first.
ALL = "all"


class Browser:
    """One library, narrowed three ways, with a cursor that survives all of them."""

    def __init__(
        self,
        library: Library,
        per_page: int | None = None,
        installed: set[tuple[str, str]] | None = None,
    ):
        self.library = library
        self.per_page = per_page or library.per_page
        self.platforms = [ALL, *library.platforms]
        self._platform_index = 0
        self.regions = [ALL, *library.regions]
        self._region_index = 0
        self.search = ""
        # On-disk keys from the client, kept off Game: this changes under the
        # grid (an uninstall), the catalog does not.
        self.installed: set[tuple[str, str]] = set(installed or ())
        self.installed_only = False
        self.grid = Grid(library)

    @property
    def platform(self) -> str:
        return self.platforms[self._platform_index]

    @property
    def region(self) -> str:
        return self.regions[self._region_index]

    @property
    def visible(self) -> Library:
        return self.grid.library

    def cycle_platform(self, delta: int) -> None:
        self._platform_index = (self._platform_index + delta) % len(self.platforms)
        self._reframe()

    def set_platform(self, platform: str) -> None:
        if platform in self.platforms:
            self._platform_index = self.platforms.index(platform)
            self._reframe()

    def cycle_region(self, delta: int) -> None:
        self._region_index = (self._region_index + delta) % len(self.regions)
        self._reframe()

    def set_region(self, region: str) -> None:
        if region in self.regions:
            self._region_index = self.regions.index(region)
            self._reframe()

    def set_search(self, text: str) -> None:
        self.search = text
        self._reframe()

    def is_installed(self, game: Game) -> bool:
        return game.key in self.installed

    def toggle_installed(self) -> None:
        self.installed_only = not self.installed_only
        self._reframe()

    def set_installed(self, keys: set[tuple[str, str]]) -> None:
        """Refresh after an uninstall: badges always update; the cursor only
        moves when the filter is on, since with it off nothing changed shape."""
        self.installed = set(keys)
        if self.installed_only:
            self._reframe()

    def _reframe(self) -> None:
        """Rebuild the view, and put the cursor back at the start.

        Back to the start rather than clamped: a filter is a new question, and
        keeping page 300 across one that has four pages is how a grid ends up
        blank with nothing on screen explaining why. Grid's own clamping would
        stop it being *out of range*; it would not stop it being wrong.
        """
        found = self.library.filter(
            platform=None if self.platform == ALL else self.platform,
            region=None if self.region == ALL else self.region,
            search=self.search or None,
        )
        games = found.games
        if self.installed_only:
            games = [g for g in games if g.key in self.installed]
        self.grid = Grid(Library(games, self.per_page))

    @property
    def status(self) -> str:
        """The line under the grid — what is being looked at, and how much."""
        total = len(self.visible)
        if not total:
            bits = ["no games"]
        else:
            bits = [f"page {self.grid.page_index + 1} of {self.grid.library.pages}", f"{total} games"]
        if self.platform != ALL:
            bits.append(self.platform)
        if self.region != ALL:
            bits.append(f"region {self.region}+world" if self.region != "world" else "region world")
        if self.search:
            bits.append(f'"{self.search}"')
        if self.installed_only:
            bits.append("installed only")
        return "  ·  ".join(bits)
