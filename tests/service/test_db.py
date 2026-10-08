"""The one way this service opens SQLite.

The token store and the catalog each carried an identical `_connect`: WAL, a
five second busy timeout, foreign keys on, rows addressable by name, and a
connection that may be used from the thread that did not open it. What those
five settings are is pinned here, once, instead of by whichever store a test
happens to exercise.
"""

from __future__ import annotations

import sqlite3
import threading

from gotg import _db


def test_a_connection_has_the_settings_both_stores_relied_on(tmp_path):
    conn = _db.connect(tmp_path / "x.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_a_connection_may_be_used_from_another_thread(tmp_path):
    conn = _db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE t (n INTEGER)")
    outcome: list[object] = []

    def use():
        try:
            outcome.append(conn.execute("SELECT 1 AS one").fetchone()["one"])
        except Exception as error:  # noqa: BLE001
            outcome.append(error)

    thread = threading.Thread(target=use)
    thread.start()
    thread.join()
    conn.close()
    assert outcome == [1]


def test_a_foreign_key_is_enforced(tmp_path):
    conn = _db.connect(tmp_path / "x.db")
    conn.executescript("CREATE TABLE p (id INTEGER PRIMARY KEY);CREATE TABLE c (p INTEGER REFERENCES p(id));")
    try:
        conn.execute("INSERT INTO c VALUES (99)")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("a child row with no parent was accepted")
    finally:
        conn.close()
