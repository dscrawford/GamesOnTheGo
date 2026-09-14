"""The fleet's tile pictures, held once and served to everybody.

Every client used to resolve artwork for itself: a SteamGridDB search, an
asset query and a download, per game, per machine. A library of 5674 entries
times a handful of machines is tens of thousands of questions against one
shared key for a set of answers that are identical every time — and most of
them are "no", because libretro has no Switch playlist and plenty of No-Intro
names match nothing.

So the answers live here instead. A warmer (`gotg art warm`) resolves each
game once, slowly, and writes the result through the index token; every other
machine asks this service and never touches an upstream at all. The negative
half is stored too, for the same reason the client's own cache stores it: a
miss that is not written down is re-asked forever.

Laid out as <root>/<platform>/<id>.<ext>, which is the client's layout
(src/ui/gotg_ui/art.py) on purpose — the same names mean a directory can be
copied either way, and a picture can be looked at with an image viewer when
somebody wonders whether a tile is wrong or simply missing.

Stdlib only, like the rest of the service.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..contract import ENTRY_ID_RE, PLATFORM_RE

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
RIFF = b"RIFF"
WEBP = b"WEBP"

EXTENSIONS = (".png", ".jpg", ".webp")
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
MISS_SUFFIX = ".miss"

# The entry-id contract puts no ceiling on an id; the filesystem does. 255
# bytes per name on ext4, minus the longest suffix written here.
MAX_ID = 240


def extension_for(body: bytes) -> str | None:
    """What kind of picture this is, from its own bytes rather than from what
    the uploader claimed. A Content-Type is a client's word for it; the magic
    is the file's."""
    if body.startswith(PNG):
        return ".png"
    if body.startswith(JPEG):
        return ".jpg"
    if body.startswith(RIFF) and body[8:12] == WEBP:
        return ".webp"
    return None


class ArtCache:
    """The pictures on disk, and the record of which games had none."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # --- names --------------------------------------------------------------

    def _dir(self, platform: str, game_id: str) -> Path:
        """One directory per platform, and nothing that is not an id gets to
        name a path.

        Both halves arrive from the wire and end up as path components that
        later meet unlink(). The patterns are the contract's own, so what is
        storable here is exactly what is catalogable — a stricter invention
        would silently refuse real games, which is how the client's first
        draft lost 127 tiles to a 64-character cap.
        """
        if not PLATFORM_RE.match(platform) or not ENTRY_ID_RE.match(game_id) or len(game_id) > MAX_ID:
            raise ValueError(f"not a storable name: {platform}/{game_id}")
        return self.root / platform

    def path_for(self, platform: str, game_id: str, extension: str = "") -> Path:
        """Keyed on platform *and* id: 222 ids in this library are on more
        than one platform, so an id alone would have gb's Asterix overwrite
        snes'."""
        return self._dir(platform, game_id) / f"{game_id}{extension}"

    # --- reading ------------------------------------------------------------

    def get(self, platform: str, game_id: str) -> tuple[Path, str] | None:
        """The picture and its extension, or None."""
        for extension in EXTENSIONS:
            candidate = self.path_for(platform, game_id, extension)
            if candidate.exists():
                return candidate, extension
        return None

    def is_miss(self, platform: str, game_id: str) -> bool:
        return self.path_for(platform, game_id, MISS_SUFFIX).exists()

    def index(self) -> dict:
        """Everything known, in one answer.

        A client that has just started wants to fill its whole cache, and the
        alternative is 5674 conditional GETs to find out that 4000 of them are
        misses. So the index is a scan — one stat per file, no bodies read —
        and the client turns it into exactly the downloads that are worth
        making.

        The shape is deliberately flat: "<platform>/<id>" keys, because that
        is the path the client will GET next.
        """
        art: dict[str, dict] = {}
        misses: list[str] = []
        for platform in self._subdirectories(self.root):
            for entry in self._subdirectories(platform, files=True):
                stem, extension = os.path.splitext(entry.name)
                key = f"{platform.name}/{stem}"
                if extension == MISS_SUFFIX:
                    misses.append(key)
                elif extension in CONTENT_TYPES:
                    art[key] = {"ext": extension, "bytes": entry.stat().st_size}
        return {"version": 1, "art": art, "misses": sorted(misses)}

    @staticmethod
    def _subdirectories(where, *, files: bool = False) -> list:
        """The entries of one directory, or nothing. A cache directory that
        does not exist yet is an empty index, not a 500: the volume is
        mounted before the first warm has ever run."""
        target = where if isinstance(where, Path) else Path(where.path)
        try:
            with os.scandir(target) as entries:
                return sorted(
                    (entry for entry in entries if (entry.is_file() if files else entry.is_dir())),
                    key=lambda entry: entry.name,
                )
        except OSError:
            return []

    # --- writing ------------------------------------------------------------

    def put(self, platform: str, game_id: str, body: bytes) -> Path:
        extension = extension_for(body)
        if extension is None:
            raise ValueError("not an image")
        # Replace rather than accumulate: a game whose art changes format
        # would otherwise keep both, and get() would answer with whichever
        # extension sorts first.
        self.forget(platform, game_id)
        directory = self._dir(platform, game_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(platform, game_id, extension)
        # Written beside the destination and renamed, so a warmer killed
        # mid-upload never leaves half a picture that reads as cached.
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        return path

    def put_miss(self, platform: str, game_id: str) -> Path:
        """Write down that nothing has this one. The whole point of warming:
        a miss recorded here is a question no machine ever asks an upstream
        again."""
        self.forget(platform, game_id)
        directory = self._dir(platform, game_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(platform, game_id, MISS_SUFFIX)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(b"")
        os.replace(tmp, path)
        return path

    def forget(self, platform: str, game_id: str) -> bool:
        """Drop everything known about one game, so the next warm looks
        again. Returns whether there was anything to drop."""
        dropped = False
        for extension in (*EXTENSIONS, MISS_SUFFIX):
            path = self.path_for(platform, game_id, extension)
            try:
                path.unlink()
                dropped = True
            except OSError:
                pass
        return dropped
