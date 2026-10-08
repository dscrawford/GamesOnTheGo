"""Players' session logs, kept for debugging and capped per user.

A bundle is opaque here beyond its zstd magic: the client builds it, an admin
unpacks it. What the store owns is the quota (oldest sessions go first, the
newcomer never does) and that an id or a user becomes a path only after
passing its shape check.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .contract import utc_now
from .saves import USER_RE

SESSION_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-env-[a-z0-9_-]+$")
MAX_ID_LENGTH = 200
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

DEFAULT_QUOTA_BYTES = 262_144_000
DEFAULT_MAX_UPLOAD_BYTES = 33_554_432


class TooLarge(Exception):
    """A session over the upload cap, or alone over the user's quota."""


@dataclass(frozen=True)
class Kept:
    bytes: int
    sessions: int
    dropped: list[str]


def valid_session_id(session_id: str) -> bool:
    return len(session_id) <= MAX_ID_LENGTH and SESSION_RE.fullmatch(session_id) is not None


def is_zstd(body: bytes) -> bool:
    return body.startswith(ZSTD_MAGIC)


def pick_victims(entries: list[tuple[str, str, int]], quota: int, keep: str) -> list[str]:
    """Ids to delete, oldest first, until the rest fit `quota`; `keep` is never one.

    `entries` are `(id, at, bytes)`. Ids start with the session's start time,
    so they break ties between sessions received in the same second.
    """
    total = sum(size for _, _, size in entries)
    victims = []
    for session_id, _, size in sorted(entries, key=lambda e: (e[1], e[0])):
        if total <= quota:
            break
        if session_id == keep:
            continue
        victims.append(session_id)
        total -= size
    return victims


@dataclass
class LogsStore:
    root: Path
    quota_bytes: int = DEFAULT_QUOTA_BYTES
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    clock: Callable[[], float] = time.time
    # One lock, as the saves store has: a few uploads a day need no finer grain.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _user_dir(self, user: str) -> Path:
        if not USER_RE.match(user):
            raise ValueError(f"invalid user name: {user}")
        return self.root / user

    def path(self, user: str, session_id: str) -> Path | None:
        """The bundle's file, or None when it is not kept (or the names are not ones)."""
        if not USER_RE.match(user) or not valid_session_id(session_id):
            return None
        found = self._user_dir(user) / f"{session_id}.tar.zst"
        return found if found.is_file() else None

    def list_user(self, user: str) -> list[dict]:
        """A user's sessions, oldest first. A bundle with no readable record is
        not listed, so nothing the quota cannot count is ever served."""
        directory = self._user_dir(user)
        if not directory.is_dir():
            return []
        found = []
        for record in directory.glob("*.json"):
            session_id = record.stem
            if not valid_session_id(session_id) or not (directory / f"{session_id}.tar.zst").is_file():
                continue
            try:
                meta = json.loads(record.read_text())
                found.append({"id": session_id, **{k: meta[k] for k in ("bytes", "at", "attr", "device")}})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(found, key=lambda e: (e["at"], e["id"]))

    def list_all(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        users = []
        for directory in sorted(self.root.iterdir()):
            if not USER_RE.match(directory.name) or not directory.is_dir():
                continue
            sessions = self.list_user(directory.name)
            if sessions:
                users.append({"user": directory.name, "bytes": sum(s["bytes"] for s in sessions), "sessions": sessions})
        return users

    def put(self, user: str, session_id: str, body: bytes, attr: str, device: str) -> Kept:
        """Store a session, then drop the user's oldest until they fit.

        ValueError for a bad user, id or non-zstd body; TooLarge when the body
        alone could never be kept. The refusal comes before anything is
        written, so a rejected upload costs nobody else their history.
        """
        directory = self._user_dir(user)
        if not valid_session_id(session_id):
            raise ValueError(f"not a session id: {session_id!r}")
        if not is_zstd(body):
            raise ValueError("a session bundle is a zstd-compressed tar")
        if len(body) > self.max_upload_bytes or len(body) > self.quota_bytes:
            raise TooLarge(f"{len(body)} bytes is more than a session may be")

        with self._lock:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            record = {"at": utc_now(self.clock()), "bytes": len(body), "attr": attr, "device": device}
            self._write(directory / f"{session_id}.tar.zst", body)
            self._write(directory / f"{session_id}.json", json.dumps(record).encode())

            entries = [(e["id"], e["at"], e["bytes"]) for e in self.list_user(user)]
            dropped = pick_victims(entries, self.quota_bytes, keep=session_id)
            for victim in dropped:
                self._remove(directory, victim)
            return Kept(len(body), len(entries) - len(dropped), dropped)

    def delete(self, user: str, session_id: str) -> bool:
        if self.path(user, session_id) is None:
            return False
        with self._lock:
            self._remove(self._user_dir(user), session_id)
        return True

    @staticmethod
    def _write(target: Path, data: bytes) -> None:
        tmp = target.with_suffix(".part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        tmp.replace(target)

    @staticmethod
    def _remove(directory: Path, session_id: str) -> None:
        # The bundle goes first: a record with no bundle is unlisted, a bundle
        # with no record would be uncounted.
        for suffix in (".tar.zst", ".json"):
            (directory / f"{session_id}{suffix}").unlink(missing_ok=True)
