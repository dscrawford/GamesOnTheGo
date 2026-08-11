"""The games catalog: rows that say where the bytes already are.

The old importer materialized a second tree of hardlinks so that files would
carry canonical names; this table carries the names instead, and the files
stay where the torrents put them. An entry is an id, a handler — what shape
the raw source is, which the client's environment turns into a recipe — and
the member files with their sizes and hashes. One file for most games; a
scene release lists its volumes.

Consistency comes from topology, not the engine: only the service process
opens this database, and the indexer talks to the API. In-process, sqlite3
connections are thread-bound and every request runs on its own thread, so
writes go through one connection under one lock (the SavesStore pattern) and
reads get a connection per thread.

Nothing here touches the library filesystem. Rows are validated for shape and
containment when they arrive; whether a path still exists is the streaming
endpoint's problem, and deleting a row never deletes a file.
"""

from __future__ import annotations

import os.path
import re
import sqlite3
import threading
import time
from pathlib import Path

PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")
ID_RE = re.compile(r"^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The classifier's vocabulary, mirrored from the importer. A handler the
# client has no recipe for downloads and then stops with a clear message, so
# an unknown one is refused here where the indexer can see it.
HANDLERS = frozenset(
    {
        "single_file",
        "no_intro_set",
        "scene_archive",
        "single_archive",
        "wiiu_decrypted",
        "wiiu_nus",
    }
)

# How much of the catalog a sweep may report vanished without an explicit
# confirm. The failure this guards is a half-run indexer reporting the whole
# untouched library gone — which trains people to ignore the report.
SWEEP_LIMIT = 0.2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entry (
  platform    TEXT NOT NULL,
  id          TEXT NOT NULL,
  handler     TEXT NOT NULL,
  title       TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  seen_at     TEXT NOT NULL,
  PRIMARY KEY (platform, id)
);
CREATE TABLE IF NOT EXISTS entry_file (
  platform   TEXT NOT NULL,
  id         TEXT NOT NULL,
  name       TEXT NOT NULL,
  path       TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime      INTEGER NOT NULL,
  sha256     TEXT,
  PRIMARY KEY (platform, id, name),
  FOREIGN KEY (platform, id) REFERENCES entry(platform, id) ON DELETE CASCADE
);
"""


class Conflict(Exception):
    """The stored entry points at different bytes than the arriving one."""

    def __init__(self, stored: dict):
        super().__init__("entry exists with different paths")
        self.stored = stored


class SweepRefused(Exception):
    def __init__(self, vanished: int, total: int):
        super().__init__(
            f"sweep would report {vanished} of {total} entries vanished; "
            "a half-run scan looks exactly like this. Repeat with confirm=1 "
            "if the library really shrank."
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def valid_filename(name: str) -> bool:
    return (
        0 < len(name) <= 255
        and name.isprintable()
        and "/" not in name
        and not name.startswith(".")
    )


class CatalogStore:
    def __init__(self, db: Path, roots: list[Path]):
        if not roots:
            raise ValueError("a catalog needs at least one library root")
        self.roots = [Path(os.path.realpath(r)) for r in roots]
        self.db = Path(db)

        # A row grants read on its path, so the database must not be able to
        # name itself or anything else the service writes.
        for root in self.roots:
            if Path(os.path.realpath(self.db)).is_relative_to(root):
                raise ValueError(f"catalog db {db} is inside library root {root}")

        self.db.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._write = self._connect()
        with self._write_lock, self._write:
            self._write.executescript(_SCHEMA)
        self._local = threading.local()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _read(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _contained(self, path: str) -> bool:
        # realpath rather than resolve(strict=True): the service may not see
        # the library at all (tests, a proxy-only deployment), and existence
        # is the streaming endpoint's question. Containment is still checked
        # against the resolved form so a symlinked prefix cannot lie.
        real = Path(os.path.realpath(path))
        return any(real.is_relative_to(root) for root in self.roots)

    # --- validation ---------------------------------------------------------

    def _validate(self, platform: str, game_id: str, payload: dict) -> dict:
        if not PLATFORM_RE.match(platform):
            raise ValueError(f"invalid platform: {platform!r}")
        if not ID_RE.match(game_id):
            raise ValueError(f"invalid game id: {game_id!r}")

        handler = payload.get("handler", "")
        if handler not in HANDLERS:
            raise ValueError(f"unknown handler: {handler!r}")
        title = payload.get("title", "")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("an entry needs a title")

        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("an entry needs at least one file")

        seen_names = set()
        cleaned = []
        for member in files:
            if not isinstance(member, dict):
                raise ValueError("each file must be an object")
            name = member.get("name", "")
            if not isinstance(name, str) or not valid_filename(name):
                raise ValueError(f"invalid file name: {name!r}")
            if name in seen_names:
                raise ValueError(f"duplicate file name: {name!r}")
            seen_names.add(name)

            path = member.get("path", "")
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError(f"file path must be absolute: {path!r}")
            if not self._contained(path):
                raise ValueError(f"file path is outside every library root: {path!r}")

            size = member.get("size_bytes")
            mtime = member.get("mtime")
            if not isinstance(size, int) or size < 0:
                raise ValueError(f"invalid size for {name!r}")
            if not isinstance(mtime, int) or mtime < 0:
                raise ValueError(f"invalid mtime for {name!r}")

            sha = member.get("sha256")
            if sha is not None and (not isinstance(sha, str) or not SHA256_RE.match(sha)):
                raise ValueError(f"invalid sha256 for {name!r}")

            cleaned.append(
                {"name": name, "path": path, "size_bytes": size, "mtime": mtime, "sha256": sha}
            )

        return {"handler": handler, "title": title.strip(), "files": cleaned}

    # --- writes -------------------------------------------------------------

    def upsert(self, platform: str, game_id: str, payload: dict, *, force: bool = False) -> dict:
        entry = self._validate(platform, game_id, payload)
        now = _now()

        with self._write_lock, self._write:
            stored = self._entry(self._write, platform, game_id, full=True)
            if stored is not None and not force:
                # Two torrents producing the same id is a real error the old
                # hardlink collision used to surface; an upsert must not
                # swallow it. Same bytes moving is fine — same id from a
                # different source is not.
                if {f["path"] for f in stored["files"]} != {f["path"] for f in entry["files"]}:
                    raise Conflict(stored)

            imported_at = stored["imported_at"] if stored else now
            self._write.execute(
                "INSERT INTO entry (platform, id, handler, title, imported_at, seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (platform, id) DO UPDATE SET"
                " handler=excluded.handler, title=excluded.title, seen_at=excluded.seen_at",
                (platform, game_id, entry["handler"], entry["title"], imported_at, now),
            )
            self._write.execute(
                "DELETE FROM entry_file WHERE platform = ? AND id = ?", (platform, game_id)
            )
            self._write.executemany(
                "INSERT INTO entry_file (platform, id, name, path, size_bytes, mtime, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (platform, game_id, f["name"], f["path"], f["size_bytes"], f["mtime"], f["sha256"])
                    for f in entry["files"]
                ],
            )
            written = self._entry(self._write, platform, game_id, full=True)
            if written is None:  # unreachable: written under the same lock
                raise RuntimeError("entry vanished during upsert")
            return written

    def delete(self, platform: str, game_id: str) -> bool:
        with self._write_lock, self._write:
            cursor = self._write.execute(
                "DELETE FROM entry WHERE platform = ? AND id = ?", (platform, game_id)
            )
            return cursor.rowcount > 0

    def sweep(self, since: str, *, confirm: bool = False) -> dict:
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", since):
            raise ValueError(f"since must be an ISO UTC timestamp, got {since!r}")
        conn = self._read()
        total = conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
        rows = conn.execute(
            "SELECT platform, id, seen_at FROM entry WHERE seen_at < ? ORDER BY platform, id",
            (since,),
        ).fetchall()
        if total and len(rows) / total > SWEEP_LIMIT and not confirm:
            raise SweepRefused(len(rows), total)
        return {
            "total": total,
            "vanished": [dict(row) for row in rows],
        }

    # --- reads --------------------------------------------------------------

    def _entry(self, conn: sqlite3.Connection, platform: str, game_id: str, *, full: bool) -> dict | None:
        row = conn.execute(
            "SELECT * FROM entry WHERE platform = ? AND id = ?", (platform, game_id)
        ).fetchone()
        if row is None:
            return None
        files = conn.execute(
            "SELECT name, path, size_bytes, mtime, sha256 FROM entry_file"
            " WHERE platform = ? AND id = ? ORDER BY name",
            (platform, game_id),
        ).fetchall()
        entry = {
            "id": row["id"],
            "platform": row["platform"],
            "handler": row["handler"],
            "title": row["title"],
            "files": [self._file_view(f, full=full) for f in files],
        }
        if full:
            entry["imported_at"] = row["imported_at"]
            entry["seen_at"] = row["seen_at"]
        return entry

    @staticmethod
    def _file_view(row: sqlite3.Row, *, full: bool) -> dict:
        view = {"name": row["name"], "size_bytes": row["size_bytes"], "sha256": row["sha256"]}
        if full:
            view["path"] = row["path"]
            view["mtime"] = row["mtime"]
        return view

    def view(self, *, full: bool = False) -> dict:
        conn = self._read()
        keys = conn.execute("SELECT platform, id FROM entry ORDER BY platform, id").fetchall()
        return {
            "version": 2,
            "games": [self._entry(conn, k["platform"], k["id"], full=full) for k in keys],
        }

    def lookup(self, platform: str, game_id: str, name: str) -> Path | None:
        """The absolute path behind one member file, containment re-checked.

        Used by the streaming endpoint; None means 404 whichever half is
        missing, so a caller cannot probe which ids exist without also being
        allowed to read them.
        """
        if not (PLATFORM_RE.match(platform) and ID_RE.match(game_id) and valid_filename(name)):
            return None
        row = self._read().execute(
            "SELECT path FROM entry_file WHERE platform = ? AND id = ? AND name = ?",
            (platform, game_id, name),
        ).fetchone()
        if row is None or not self._contained(row["path"]):
            return None
        return Path(row["path"])
