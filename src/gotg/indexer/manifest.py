"""The catalog the client downloads (IMPORTER_SPEC.md §8).

Lives at ``<GAMES_ROOT>/.gotg/manifest.json``; the dot-directory keeps it out of the
File Browser listing people see. Entries are keyed by server path and merged, so a
run only ever adds or updates the games it touched.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class Entry:
    """One downloadable game, exactly as the client consumes it."""

    platform: str
    path: str  # server-relative, e.g. /Games/n64/usa.foo.z64
    type: str  # "file" | "dir"
    size_bytes: int
    sha256: str | None
    title: str

    @property
    def game_id(self) -> str:
        """The GOTG id: the entry name minus its extension."""
        name = self.path.rsplit("/", 1)[-1]
        return name.rsplit(".", 1)[0] if self.type == "file" and "." in name else name


def load(path: Path) -> dict[str, Entry]:
    """Read the manifest, keyed by server path. Missing or corrupt reads as empty."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}

    entries: dict[str, Entry] = {}
    for item in raw.get("games", []):
        try:
            entries[item["path"]] = Entry(
                platform=item["platform"],
                path=item["path"],
                type=item.get("type", "file"),
                size_bytes=int(item.get("size_bytes", 0)),
                sha256=item.get("sha256"),
                title=item.get("title", ""),
            )
        except (KeyError, TypeError, ValueError):
            continue  # drop unreadable rows rather than failing the whole catalog
    return entries


def save(path: Path, entries: dict[str, Entry]) -> bool:
    """Atomically replace the manifest, sorted for a stable diff.

    Returns whether anything was written. An unchanged catalog is left alone so a
    CronJob that finds no new games does not rewrite this file every five minutes.
    """
    path = Path(path)
    payload = {
        "version": MANIFEST_VERSION,
        "games": [asdict(entries[key]) for key in sorted(entries)],
    }
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)

    if path.exists():
        try:
            if path.read_text() == serialized:
                return False
        except OSError:
            pass

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(serialized)
    os.replace(tmp, path)
    return True
