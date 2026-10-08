"""How this service opens SQLite, in one place.

The token store and the catalog each had a byte-identical `_connect`. They are
the same decision -- WAL so readers do not block the writer, a five second
busy timeout so a contended write waits instead of failing, foreign keys
because SQLite leaves them off, rows by name, and `check_same_thread=False`
because the server runs a thread per connection and the stores hand a
connection across with their own locks -- and a third store would have copied
it a third time. Stdlib only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
