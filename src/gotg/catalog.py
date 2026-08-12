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

import os
import os.path
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .contract import ENTRY_ID_RE as ID_RE
from .contract import (
    HANDLERS,
    PLATFORM_RE,
    SHA256_RE,
    valid_filename,
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # Opened per operation and closed deterministically, not cached per
    # thread: the server runs a thread per TCP connection, and a connection
    # parked in threading.local outlives its thread until a full GC pass —
    # measured as dozens of stale fds for zero reuse. Opening costs a fraction
    # of the query it serves.
    @contextmanager
    def _read(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _contained(self, path: str) -> bool:
        # realpath rather than resolve(strict=True): the service may not see
        # the library at all (tests, a proxy-only deployment), and existence
        # is the streaming endpoint's question. Containment is still checked
        # against the resolved form so a symlinked prefix cannot lie.
        real = Path(os.path.realpath(path))
        return any(real.is_relative_to(root) for root in self.roots)

    # --- validation ---------------------------------------------------------

    def _validate(self, platform: str, game_id: str, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("an entry must be a JSON object")
        if not PLATFORM_RE.match(platform):
            raise ValueError(f"invalid platform: {platform!r}")
        if not ID_RE.match(game_id):
            raise ValueError(f"invalid game id: {game_id!r}")

        handler = payload.get("handler", "")
        if handler not in HANDLERS:
            raise ValueError(f"unknown handler: {handler!r}")
        title = payload.get("title", "")
        # Titles end up in client terminal output.
        if not isinstance(title, str) or not title.strip() or not title.isprintable():
            raise ValueError("an entry needs a printable title")

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
            # type() is int, not isinstance: bool passes isinstance and JSON
            # true would land as 1. The upper bound is SQLite's INTEGER — an
            # overflow inside the write transaction is a dropped connection.
            if type(size) is not int or not 0 <= size < 2**63:
                raise ValueError(f"invalid size for {name!r}")
            if type(mtime) is not int or not 0 <= mtime < 2**63:
                raise ValueError(f"invalid mtime for {name!r}")

            sha = member.get("sha256")
            if sha is not None and (not isinstance(sha, str) or not SHA256_RE.match(sha)):
                raise ValueError(f"invalid sha256 for {name!r}")

            cleaned.append({"name": name, "path": path, "size_bytes": size, "mtime": mtime, "sha256": sha})

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
            self._write.execute("DELETE FROM entry_file WHERE platform = ? AND id = ?", (platform, game_id))
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
            cursor = self._write.execute("DELETE FROM entry WHERE platform = ? AND id = ?", (platform, game_id))
            return cursor.rowcount > 0

    def sweep(self, since: str, *, confirm: bool = False) -> dict:
        # [0-9], not \d: \d matches Unicode digits, which collate above ASCII
        # and turn the lexicographic seen_at comparison into nonsense.
        if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", since):
            raise ValueError(f"since must be an ISO UTC timestamp, got {since!r}")
        with self._read() as conn:
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
        row = conn.execute("SELECT * FROM entry WHERE platform = ? AND id = ?", (platform, game_id)).fetchone()
        if row is None:
            return None
        files = conn.execute(
            "SELECT name, path, size_bytes, mtime, sha256 FROM entry_file WHERE platform = ? AND id = ? ORDER BY name",
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
        # Two queries under one read transaction, not one per entry: a delete
        # landing mid-iteration must not put a null in the games array, and a
        # WAL snapshot held across both queries is what rules it out.
        with self._read() as conn:
            conn.execute("BEGIN")
            try:
                entries = conn.execute("SELECT * FROM entry ORDER BY platform, id").fetchall()
                files = conn.execute(
                    "SELECT platform, id, name, path, size_bytes, mtime, sha256"
                    " FROM entry_file ORDER BY platform, id, name"
                ).fetchall()
            finally:
                conn.execute("COMMIT")

        by_entry: dict[tuple[str, str], list] = {}
        for row in files:
            by_entry.setdefault((row["platform"], row["id"]), []).append(self._file_view(row, full=full))
        games = []
        for row in entries:
            game = {
                "id": row["id"],
                "platform": row["platform"],
                "handler": row["handler"],
                "title": row["title"],
                "files": by_entry.get((row["platform"], row["id"]), []),
            }
            if full:
                game["imported_at"] = row["imported_at"]
                game["seen_at"] = row["seen_at"]
            games.append(game)
        return {"version": 2, "games": games}

    def lookup(self, platform: str, game_id: str, name: str) -> Path | None:
        """The resolved path behind one member file, containment-checked.

        None means 404 whichever half is missing, so a caller cannot probe
        which ids exist without also being allowed to read them. This answers
        a question about the catalog; actually reading the file goes through
        open_member, which is immune to the path being swapped underneath.
        """
        if not (PLATFORM_RE.match(platform) and ID_RE.match(game_id) and valid_filename(name)):
            return None
        with self._read() as conn:
            row = conn.execute(
                "SELECT path FROM entry_file WHERE platform = ? AND id = ? AND name = ?",
                (platform, game_id, name),
            ).fetchone()
        if row is None or not self._contained(row["path"]):
            return None
        return Path(os.path.realpath(row["path"]))

    def open_member(self, platform: str, game_id: str, name: str) -> tuple[dict, int | None] | None:
        """One member's metadata and an open fd — or fd None for a stale row,
        or None outright when the catalog has no such member.

        One query serves both, on purpose: metadata and bytes read in two
        snapshots could pair an old sha256 (sent as the ETag that validates
        resumes) with new bytes — the exact splice If-Range exists to prevent.

        The containment check and the open are separate syscalls, and anything
        that can write inside a library root — the torrent client, most of all
        — could swap a symlink in between them. So the check that counts is on
        what was actually opened: O_NOFOLLOW refuses a symlink as the final
        component, and the /proc re-check catches a retargeted directory on
        the way there. The caller owns the fd.
        """
        if not (PLATFORM_RE.match(platform) and ID_RE.match(game_id) and valid_filename(name)):
            return None
        with self._read() as conn:
            row = conn.execute(
                "SELECT name, path, size_bytes, mtime, sha256 FROM entry_file"
                " WHERE platform = ? AND id = ? AND name = ?",
                (platform, game_id, name),
            ).fetchone()
        if row is None or not self._contained(row["path"]):
            return None
        meta = self._file_view(row, full=False)
        try:
            fd = os.open(row["path"], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except OSError:
            return meta, None
        real = Path(os.path.realpath(f"/proc/self/fd/{fd}"))
        if not any(real.is_relative_to(root) for root in self.roots):
            os.close(fd)
            return meta, None
        return meta, fd
