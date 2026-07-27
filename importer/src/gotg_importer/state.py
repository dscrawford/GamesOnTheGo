"""Processed-torrent state file.

qBittorrent tags are the primary idempotency record; this file is the backstop for
when tags are lost (category re-created, torrents re-added, WebUI restored from a
backup). Losing it is harmless — the tree operations are independently idempotent.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("gotg-importer")

STATE_FILENAME = "processed.json"


class State:
    """The set of torrent infohashes this importer has already handled."""

    def __init__(self, path: Path, processed: set[str] | None = None) -> None:
        self.path = path
        self.processed: set[str] = processed or set()
        self._dirty = False

    @classmethod
    def load(cls, state_dir: Path) -> State:
        path = Path(state_dir) / STATE_FILENAME
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text())
            return cls(path, set(raw.get("processed", [])))
        except (OSError, ValueError) as exc:
            # A corrupt backstop must not stop the run; tags still guard duplicates.
            log.warning("ignoring unreadable state file %s: %s", path, exc)
            return cls(path)

    def __contains__(self, infohash: str) -> bool:
        return infohash in self.processed

    def mark(self, infohash: str) -> None:
        if infohash not in self.processed:
            self.processed.add(infohash)
            self._dirty = True

    def save(self) -> None:
        """Atomically replace the state file, if anything changed."""
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"processed": sorted(self.processed)}, indent=2))
        os.replace(tmp, self.path)
        self._dirty = False
