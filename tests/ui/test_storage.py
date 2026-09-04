"""The storage screen's seam to the client: what it asks, what it shows, and
that a client that cannot answer leaves a message rather than a crash."""

from __future__ import annotations

import json
import stat

from gotg_ui.storage import Storage, human, list_dirs


def stub(tmp_path, body: str, monkeypatch) -> str:
    path = tmp_path / "gotg"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GOTG_BIN", str(path))
    return str(path)


ROWS = [
    {"path": "/home/x/Games", "default": True, "exists": True, "free_bytes": 10, "total_bytes": 100},
    {"path": "/run/media/card/Games", "default": False, "exists": True, "free_bytes": 5, "total_bytes": 50},
]


def test_listing_asks_the_client_for_json(tmp_path, monkeypatch):
    stub(tmp_path, f"echo \"$@\" > {tmp_path}/argv; printf '%s' '{json.dumps(ROWS)}'", monkeypatch)
    assert [r["path"] for r in list_dirs()] == ["/home/x/Games", "/run/media/card/Games"]
    assert (tmp_path / "argv").read_text().split() == ["configure", "storage", "list", "--json"]


def test_a_failing_or_garbled_client_lists_nothing(tmp_path, monkeypatch):
    stub(tmp_path, "echo boom >&2; exit 1", monkeypatch)
    assert list_dirs() == []
    stub(tmp_path, "printf 'not json'", monkeypatch)
    assert list_dirs() == []
    stub(tmp_path, 'printf \'[1, {"nope": 1}, {"path": "/a"}]\'', monkeypatch)
    assert [r["path"] for r in list_dirs()] == ["/a"]


def test_the_cursor_walks_and_clamps():
    s = Storage(list(ROWS))
    assert s.row["path"] == "/home/x/Games"
    s.move(1)
    s.move(1)
    assert s.row["path"] == "/run/media/card/Games"
    s.move(-5)
    assert s.selected == 0
    assert Storage([]).row is None


def test_changes_go_through_the_client_and_its_words_come_back(tmp_path, monkeypatch):
    stub(
        tmp_path,
        f"""
case "$3" in
  list) printf '%s' '{json.dumps(ROWS)}' ;;
  *) echo "$@" >> {tmp_path}/argv; echo "did $3 $4" >&2 ;;
esac
""",
        monkeypatch,
    )
    s = Storage()
    s.refresh()
    s.move(1)
    s.make_default()
    assert s.message == "did default /run/media/card/Games"
    s.remove()
    assert s.message == "did remove /run/media/card/Games"
    s.add("  /new/place ")
    assert s.message == "did add /new/place"
    s.add("   ")
    calls = (tmp_path / "argv").read_text().splitlines()
    assert calls == [
        "configure storage default /run/media/card/Games",
        "configure storage remove /run/media/card/Games",
        "configure storage add /new/place",
    ]


def test_a_refusal_shows_the_clients_last_line(tmp_path, monkeypatch):
    stub(tmp_path, "echo 'error: refusing to remove the last games directory' >&2; exit 1", monkeypatch)
    s = Storage(list(ROWS))
    s.remove()
    assert s.message == "error: refusing to remove the last games directory"


def test_a_missing_client_is_a_message_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("GOTG_BIN", str(tmp_path / "nope"))
    s = Storage(list(ROWS))
    s.make_default()
    assert s.message.startswith("could not run the client")
    assert s.rows == []


def test_human_sizes():
    assert human(0) == "0 B"
    assert human(512) == "512 B"
    assert human(2048) == "2.0 KB"
    assert human(3 * 1024**3) == "3.0 GB"
    assert human(5 * 1024**4) == "5.0 TB"
