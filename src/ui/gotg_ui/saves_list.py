"""Going back to an earlier save of one game, from its menu.

The client keeps every save worth going back to -- the service's last ten
generations and this machine's own archives, made before every pull and
restore -- and lists them (`gotg saves list --json`), newest first, with who
made each and when. This screen is that list: walk it, press A on a save, and
a prompt says what loading it means; A again restores it (`gotg saves restore`,
through the loader) and the game starts from it. B steps back out, one level
at a time.

Only for a game with an environment of its own. Saves belong to environments,
and a platform's (env-n64) holds every game on it that has no file of its
own, so going back there would rewind all of them at once -- a different
game's progress lost to a choice made about this one. Whether a game has its
own is the same question the client asks: is there a file for it.

The model is pure and the list is fetched on a worker (`fetch`), because the
client bundles and hashes what is here to say which save it is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .catalog import Game
from .client import ask
from .saves_choice import when
from .variants import env_dir

# Bundling the saves and asking the service. Longer than this and the screen
# says it could not be had, rather than spinning.
LIST_TIMEOUT = 30

# What `gotg saves restore` takes back. Anything else from the client is not
# offered: the id becomes an argument.
ID = re.compile(r"^(remote:[0-9]+|local:[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z-[0-9a-f]{12}\.tar\.zst)$")

# `play <id> emulate` is the platform's own environment by definition.
EMULATE = "emulate"


@dataclass(frozen=True)
class Entry:
    """One save: which, made where, when, and whether it is what is here."""

    id: str
    source: str  # "remote" for the service's, "local" for this machine's archive
    device: str
    written_at: str
    here: bool


@dataclass(frozen=True)
class Listing:
    dedicated: bool
    offline: bool
    entries: tuple[Entry, ...]


def parse(output: str) -> Listing | None:
    """The client's `saves list --json`, or None for anything else."""
    for line in reversed(output.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("saves"), list):
            continue
        entries = tuple(
            Entry(
                id=str(save["id"]),
                source=str(save.get("source") or ""),
                device=str(save.get("device") or ""),
                written_at=str(save.get("written_at") or ""),
                here=bool(save.get("here")),
            )
            for save in data["saves"]
            if isinstance(save, dict) and ID.match(str(save.get("id") or ""))
        )
        return Listing(dedicated=bool(data.get("dedicated")), offline=bool(data.get("offline")), entries=entries)
    return None


def fetch(game: Game, variant: str | None) -> Listing | None:
    """Ask the client. None when it could not answer in time or at all."""
    argv = ["saves", "list", f"{game.platform}/{game.id}", *([variant] if variant else []), "--json"]
    out = ask(argv, timeout=LIST_TIMEOUT)
    return None if out is None else parse(out)


def has_own_saves(game: Game, variant: str | None, where: Path | None = None) -> bool:
    """Whether this game -- or this mod of it -- keeps its saves to itself."""
    where = where if where is not None else env_dir()
    if where is None or variant == EMULATE:
        return False
    name = f"{game.id}.{variant}.nix" if variant else f"{game.id}.nix"
    return (where / "games" / game.platform / name).is_file()


@dataclass(frozen=True)
class Saves:
    """The screen: one game's saves, which is lit, and whether the prompt is
    up. `listing` None is the moment before the client has answered."""

    game: Game
    variant: str | None
    version: str | None
    listing: Listing | None = None
    failed: bool = False
    selected: int = 0
    confirming: bool = False

    @classmethod
    def open(cls, game: Game, variant: str | None, version: str | None) -> Saves:
        return cls(game, variant, version)

    def loaded(self, listing: Listing | None) -> Saves:
        """The client's answer: None is a list that could not be had."""
        return replace(self, listing=listing, failed=listing is None, selected=0, confirming=False)

    @property
    def entry(self) -> Entry | None:
        if self.listing is None or not self.listing.entries:
            return None
        return self.listing.entries[self.selected]

    def move(self, dy: int) -> Saves:
        if self.confirming or self.listing is None or not self.listing.entries:
            return self
        last = len(self.listing.entries) - 1
        return replace(self, selected=max(0, min(last, self.selected + dy)))

    def press_a(self) -> tuple[Saves, Entry | None]:
        """The prompt first; the save to restore second."""
        if self.entry is None:
            return self, None
        if not self.confirming:
            return replace(self, confirming=True), None
        return self, self.entry

    def press_b(self) -> Saves | None:
        """Out of the prompt, then out of the screen (None)."""
        return replace(self, confirming=False) if self.confirming else None

    def describe(self, entry: Entry) -> str:
        """The line under a save's date: where it was made, and what it is."""
        machine = entry.device or "an unknown machine"
        kind = "archived on this machine" if entry.source == "local" else "saved to the service"
        now = " · what you have now" if entry.here else ""
        return f"{machine} · {kind}{now}"

    def title(self, entry: Entry) -> str:
        return when(entry.written_at)
