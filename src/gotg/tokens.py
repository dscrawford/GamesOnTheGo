"""Per-person tokens: minted once, hashed at rest, claimed exactly once.

An invite is a single-use claim code; claiming it mints the token and hands
the plaintext over exactly once — only SHA-256 hex ever reaches the disk,
which is enough at 256 bits of entropy (slow hashes exist for guessable
passwords, and these are not guessable). Claiming a name that already has a
live token revokes it: a second invite is a rotation, not a conflict.

The SQLite discipline is catalog.py's: one write connection under one lock,
a fresh read connection per operation, WAL.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

TOKEN_PREFIX = "gotg_"
INVITE_PREFIX = "gotgi_"
TOKEN_RE = re.compile(r"^gotg_[A-Za-z0-9_-]{40,50}$")
CODE_RE = re.compile(r"^gotgi_[A-Za-z0-9_-]{40,50}$")

# Names reach JSON listings, logs and the admin CLI; the shape is the
# platform slug's, sized for a person-and-device ("daniel-deck").
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# The principals the service mints without this store.
RESERVED_NAMES = frozenset({"legacy", "indexer", "admin"})

INVITE_TTL = 7 * 24 * 3600
LAST_USED_INTERVAL = 60


class _Sentinel:
    def __init__(self, label: str):
        self._label = label

    def __repr__(self) -> str:
        return self._label


# claim() outcomes that are not a token: the code was consumed or expired
# (410 — it existed, and reuse is worth logging), or it never existed (404).
Claimed = _Sentinel("Claimed")
Absent = _Sentinel("Absent")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    display TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    revoked_at INTEGER,
    last_used_at INTEGER
);
-- One live token per name; retired rows stay, because "when was the old
-- token last seen" is the question a rotation exists to answer. A *user*
-- spans names: daniel-desktop and daniel-deck both belong to daniel, which
-- is the grain saves are shared at.
CREATE UNIQUE INDEX IF NOT EXISTS tokens_live_name ON tokens(name) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT '',
    code_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    token_ttl INTEGER,
    claimed_at INTEGER
);
"""


def default_user(name: str) -> str:
    """The person half of a person-device name: daniel-desktop -> daniel."""
    return name.split("-", 1)[0]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def mint_token() -> tuple[str, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    display = f"{TOKEN_PREFIX}…{token[-4:]}"
    return token, display


def mint_code() -> str:
    return INVITE_PREFIX + secrets.token_urlsafe(32)


class TokenStore:
    def __init__(self, db: Path):
        self.db = Path(db)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._write = self._connect()
        with self._write_lock, self._write:
            self._write.executescript(_SCHEMA)
        self._migrate()
        self._migrate_users()

    def _migrate(self) -> None:
        # v1 declared tokens.name UNIQUE, which forced rotation to DELETE the
        # replaced row. SQLite cannot drop a column constraint in place, so
        # the one shape change is a rebuild — idempotent, and a no-op on any
        # store minted since.
        with self._write_lock, self._write:
            row = self._write.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tokens'").fetchone()
            # v1's tell is the constraint on *name*; token_hash is UNIQUE in
            # every version, so matching bare "UNIQUE" would rebuild always.
            if row is None or "name TEXT NOT NULL UNIQUE" not in row["sql"]:
                return
            self._write.executescript(
                """
                ALTER TABLE tokens RENAME TO tokens_v1;
                CREATE TABLE tokens (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    user TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL UNIQUE,
                    display TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    revoked_at INTEGER,
                    last_used_at INTEGER
                );
                INSERT INTO tokens (id, name, token_hash, display, created_at, expires_at, revoked_at, last_used_at)
                    SELECT * FROM tokens_v1;
                DROP TABLE tokens_v1;
                CREATE UNIQUE INDEX IF NOT EXISTS tokens_live_name
                    ON tokens(name) WHERE revoked_at IS NULL;
                """
            )

    def _migrate_users(self) -> None:
        # Rows minted before users existed carry '': backfilled from the
        # person-device naming convention the names already follow, so
        # daniel-desktop's saves land where daniel-deck's do.
        with self._write_lock, self._write:
            cols = {r["name"] for r in self._write.execute("PRAGMA table_info(tokens)")}
            if "user" not in cols:
                self._write.executescript(
                    "ALTER TABLE tokens ADD COLUMN user TEXT NOT NULL DEFAULT '';"
                    "ALTER TABLE invites ADD COLUMN user TEXT NOT NULL DEFAULT '';"
                )
            for table in ("tokens", "invites"):
                for row in self._write.execute(f"SELECT id, name FROM {table} WHERE user = ''").fetchall():  # noqa: S608
                    self._write.execute(
                        f"UPDATE {table} SET user = ? WHERE id = ?",  # noqa: S608
                        (default_user(row["name"]), row["id"]),
                    )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _read(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def mint_invite(
        self, name: str, ttl: float = INVITE_TTL, token_ttl: float | None = None, user: str | None = None
    ) -> str:
        if not NAME_RE.match(name) or name in RESERVED_NAMES:
            raise ValueError(f"not a usable token name: {name!r}")
        user = default_user(name) if user is None else user
        if not NAME_RE.match(user) or user in RESERVED_NAMES:
            raise ValueError(f"not a usable user name: {user!r}")
        code = mint_code()
        now = time.time()
        # token_ttl rides the invite row: people get non-expiring tokens by
        # default — revocation is the control — but an invite can carry one.
        with self._write_lock, self._write:
            self._write.execute(
                "INSERT INTO invites (name, user, code_hash, created_at, expires_at, token_ttl)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, user, _hash(code), int(now), int(now + ttl), None if token_ttl is None else int(token_ttl)),
            )
        return code

    def claim(self, code: str):
        """Exactly-once: the UPDATE carries the whole predicate, and consuming
        the invite, retiring the name's old token and inserting the new one
        are one transaction — a crash cannot burn a code without minting."""
        # Shape check before the write lock: claiming is unauthenticated, and
        # junk that cannot match a code would serialize against real claims.
        if not CODE_RE.match(code):
            return Absent
        now = int(time.time())
        code_hash = _hash(code)
        token, display = mint_token()
        with self._write_lock, self._write:
            consumed = self._write.execute(
                "UPDATE invites SET claimed_at = ? WHERE code_hash = ? AND claimed_at IS NULL AND expires_at > ?",
                (now, code_hash, now),
            )
            if consumed.rowcount == 0:
                row = self._write.execute("SELECT 1 FROM invites WHERE code_hash = ?", (code_hash,)).fetchone()
                return Claimed if row else Absent

            invite = self._write.execute(
                "SELECT name, user, token_ttl FROM invites WHERE code_hash = ?", (code_hash,)
            ).fetchone()
            name = invite["name"]

            # Retire, never delete: rotation must not erase the audit trail
            # of the token it replaces.
            self._write.execute(
                "UPDATE tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                (now, name),
            )
            expires = None if invite["token_ttl"] is None else int(now + invite["token_ttl"])
            self._write.execute(
                "INSERT INTO tokens (name, user, token_hash, display, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, invite["user"], _hash(token), display, now, expires),
            )
        return name, token

    def verify(self, token: str) -> str | None:
        now = time.time()
        with self._read() as conn:
            row = conn.execute(
                "SELECT name, expires_at, revoked_at, last_used_at FROM tokens WHERE token_hash = ?",
                (_hash(token),),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if row["expires_at"] is not None and row["expires_at"] <= now:
            return None
        last = row["last_used_at"]
        if last is None or now - last >= LAST_USED_INTERVAL:
            self._touch_last_used(row["name"], now)
        return row["name"]

    def _touch_last_used(self, name: str, now: float) -> None:
        # The throttle predicate rides the UPDATE: concurrent verifies at a
        # window boundary would otherwise each pay the write.
        with self._write_lock, self._write:
            self._write.execute(
                "UPDATE tokens SET last_used_at = ? WHERE name = ? AND (last_used_at IS NULL OR last_used_at <= ?)",
                (int(now), name, int(now) - LAST_USED_INTERVAL),
            )

    def revoke(self, name: str) -> bool:
        """Closes outstanding invites too: an unclaimed invite mints a
        replacement and retires the live token as it does it, so leaving one
        open would let a leaked link outlive the revocation that answered it."""
        now = int(time.time())
        with self._write_lock, self._write:
            tokens = self._write.execute(
                "UPDATE tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                (now, name),
            )
            invites = self._write.execute(
                "UPDATE invites SET claimed_at = ? WHERE name = ? AND claimed_at IS NULL AND expires_at > ?",
                (now, name, now),
            )
            return tokens.rowcount > 0 or invites.rowcount > 0

    def user_for(self, name: str) -> str | None:
        """The user a live token's name belongs to — the saves namespace."""
        with self._read() as conn:
            row = conn.execute("SELECT user FROM tokens WHERE name = ? AND revoked_at IS NULL", (name,)).fetchone()
        return row["user"] if row else None

    def tokens(self) -> list[dict]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT name, user, display, created_at, expires_at, revoked_at, last_used_at FROM tokens ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]
