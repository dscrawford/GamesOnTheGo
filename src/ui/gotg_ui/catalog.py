"""The games the grid draws, read from the cache `gotg refresh` writes.

The same file `gotg list` reads, and never the network: a picker that stalls on
a slow link is worse than one that shows a catalog an hour old, and the client
already owns refreshing it.

Nothing here knows about pygame. Paging and filtering are where a library of
five thousand either becomes navigable or does not, and that is worth being
able to test without a display.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CATALOG_VERSION = 2
PER_PAGE = 10


class CatalogError(Exception):
    """No catalog here, or not one this can read."""


@dataclass(frozen=True)
class Game:
    id: str
    platform: str
    title: str
    handler: str

    @property
    def region(self) -> str:
        """The id's own prefix — usa, eur, jpn, world — per the entry-id
        contract. Not stored separately because the id is the contract."""
        return self.id.split(".", 1)[0]

    @property
    def key(self) -> tuple[str, str]:
        """Ids repeat across platforms — usa.bugs_life is on two — so nothing
        that names a game to something else may use the id alone."""
        return (self.platform, self.id)


def default_cache_path() -> Path:
    """Where the client puts it: $GOTG_STATE_DIR/manifest.json, XDG otherwise.

    Read from the environment rather than hardcoded so a test, or somebody
    running two libraries, moves both halves at once.
    """
    state = os.environ.get("GOTG_STATE_DIR")
    if not state:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        state = os.path.join(base, "gotg")
    return Path(state) / "manifest.json"


def load(path: Path | str | None = None) -> list[Game]:
    """Every game in the cache, in the catalog's own order.

    That order is platform then id — what the service returns and what
    `gotg list` prints. Two views of one library that disagree about sequence
    is a way to lose a game you just saw.

    A row that will not read is dropped rather than fatal, as the client's own
    manifest reader does: one bad row in five thousand must not cost the grid.
    """
    path = Path(path) if path is not None else default_cache_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise CatalogError(f"no catalog cached at {path} — run: gotg refresh") from error
    except (OSError, ValueError) as error:
        raise CatalogError(f"the catalog at {path} could not be read ({error}) — run: gotg refresh") from error

    if not isinstance(raw, dict):
        raise CatalogError(f"the catalog at {path} is not a catalog — run: gotg refresh")
    if raw.get("version") != CATALOG_VERSION:
        raise CatalogError(
            f"the catalog at {path} is version {raw.get('version')!r}, not {CATALOG_VERSION} — run: gotg refresh"
        )
    rows = raw.get("games")
    if not isinstance(rows, list):
        raise CatalogError(f"the catalog at {path} names no games — run: gotg refresh")

    games: list[Game] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            games.append(
                Game(
                    id=str(row["id"]),
                    platform=str(row["platform"]),
                    title=str(row["title"]),
                    handler=str(row.get("handler", "")),
                )
            )
        except (KeyError, TypeError):
            continue
    return games


class Library:
    """A filtered list of games, paged ten at a time.

    Filtered from the first commit even though the first screen filters by
    nothing. Ten to a page suits the screen and not the library — 5674 games is
    568 pages, and nothing on page 300 is reachable by paging to it — so the
    thing the pager runs on has to be the *filtered* list, or it gets rewritten
    the day search arrives.
    """

    def __init__(self, games: list[Game], per_page: int = PER_PAGE):
        if per_page < 1:
            raise ValueError("a page holds at least one game")
        self.games = list(games)
        self.per_page = per_page

    def __len__(self) -> int:
        return len(self.games)

    @property
    def pages(self) -> int:
        return -(-len(self.games) // self.per_page)  # ceil, without the float

    @property
    def platforms(self) -> list[str]:
        """Sorted, so the filter row does not reshuffle between launches."""
        return sorted({g.platform for g in self.games})

    @property
    def regions(self) -> list[str]:
        return sorted({g.region for g in self.games})

    def page(self, index: int) -> list[Game]:
        """One page, clamped to the ends.

        Clamped rather than wrapped or empty: a stick held down runs the index
        off the end, and an empty screen with no way back is the worst of the
        three answers.
        """
        if not self.games:
            return []
        index = max(0, min(index, self.pages - 1))
        start = index * self.per_page
        return self.games[start : start + self.per_page]

    def filter(self, *, platform: str | None = None, search: str | None = None, region: str | None = None) -> Library:
        """A new view. This one is left alone, so a filter is undoable by
        keeping the library it came from.

        A world release rides through every region filter: by the id contract
        it is region-free, so "usa" means playable-as-a-usa-library rather
        than usa-tagged. Only region=world narrows to world alone.
        """
        found = self.games
        if platform:
            found = [g for g in found if g.platform == platform]
        if region:
            found = [g for g in found if g.region == region or g.region == "world"]
        if search:
            needle = search.casefold()
            found = [g for g in found if needle in g.id.casefold() or needle in g.title.casefold()]
        return Library(found, self.per_page)
