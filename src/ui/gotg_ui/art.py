"""Tile pictures: where they are kept, and which games have none.

The negative half is the point. Most of a real library has no art anywhere —
libretro has no Switch playlist at all, and plenty of No-Intro names match
nothing — and a cache that only remembered successes would ask the network
about thousands of games on every launch. So a miss is written down too, and
only a deliberate refresh looks again.

Kept under the state directory rather than the store, so it survives a
rebuild, like the catalog cache and the save archives.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .catalog import Game

# Ids and platforms arrive from the wire and become path components that later
# meet unlink(). The service checks them on the way in; this does not rely on
# that having happened.
SAFE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
RIFF = b"RIFF"
WEBP = b"WEBP"

MISS_SUFFIX = ".miss"
EXTENSIONS = (".png", ".jpg", ".webp")


def default_root() -> Path:
    state = os.environ.get("GOTG_STATE_DIR")
    if not state:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        state = os.path.join(base, "gotg")
    return Path(state) / "ui" / "art"


def extension_for(body: bytes) -> str | None:
    """What kind of picture this is, from its own bytes.

    From the magic rather than the URL: SteamGridDB serves whichever format it
    holds and the address does not always say. It also means the cache
    directory opens in an image viewer, which is worth six lines the first
    time you wonder whether a tile is wrong or simply missing.
    """
    if body.startswith(PNG):
        return ".png"
    if body.startswith(JPEG):
        return ".jpg"
    if body.startswith(RIFF) and body[8:12] == WEBP:
        return ".webp"
    return None


class ArtStore:
    """The pictures on disk, and the record of which games had none."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_root()

    def _dir(self, game: Game) -> Path:
        if not SAFE.match(game.platform) or not SAFE.match(game.id):
            raise ValueError(f"unsafe name for a cache path: {game.platform}/{game.id}")
        return self.root / game.platform

    def path_for(self, game: Game, extension: str = "") -> Path:
        """Keyed on platform *and* id. 222 ids in a real library are on more
        than one platform, so an id alone would have gb's Asterix overwrite
        snes' and back again on every launch."""
        return self._dir(game) / f"{game.id}{extension}"

    def miss_path(self, game: Game) -> Path:
        return self.path_for(game, MISS_SUFFIX)

    def get(self, game: Game) -> Path | None:
        base = self.path_for(game)
        for extension in EXTENSIONS:
            candidate = base.with_name(base.name + extension)
            if candidate.exists():
                return candidate
        return None

    def is_miss(self, game: Game) -> bool:
        return self.miss_path(game).exists()

    def put(self, game: Game, body: bytes) -> Path:
        extension = extension_for(body)
        if extension is None:
            raise ValueError("not an image")
        # Replace rather than accumulate: a game whose art changes format
        # would otherwise keep both, and get() would answer with whichever
        # extension happened to sort first.
        self.forget(game)
        directory = self._dir(game)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(game, extension)
        # Written beside the destination and renamed, so a launch interrupted
        # mid-download never leaves a half picture that reads as cached.
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        return path

    def put_miss(self, game: Game) -> None:
        directory = self._dir(game)
        directory.mkdir(parents=True, exist_ok=True)
        # The stamp is for a person reading the directory, not for the code:
        # nothing expires, and only a refresh looks again.
        self.miss_path(game).write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")

    def forget(self, game: Game) -> None:
        """Drop both halves. What a refresh does, and what put() does first."""
        self.miss_path(game).unlink(missing_ok=True)
        base = self.path_for(game)
        for extension in EXTENSIONS:
            base.with_name(base.name + extension).unlink(missing_ok=True)
            base.with_name(base.name + extension + ".part").unlink(missing_ok=True)
