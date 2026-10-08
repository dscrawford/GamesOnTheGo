"""Property under test: the client's answer about what is out of date
becomes a badge and nothing more -- and every way the answer can be missing
lands as "nothing to say", never a crash or a stray exclamation mark."""

from __future__ import annotations

import json
import stat

from gotg_ui.updates import ALERT, INSTALLED, NONE, RING, ask, badge, outdated, parse


def report(**overrides) -> str:
    data = {
        "version": 1,
        "checked_at": 1,
        "stale": False,
        "pending": False,
        "library": "/home/me/.config/gotg/library",
        "writable": True,
        "gotg": {
            "url": "github:dscrawford/GamesOnTheGo",
            "locked": "aaaa",
            "latest": "bbbb",
            "behind": True,
            "unbuilt": False,
            "available": True,
            "picker": "/nix/store/x-gotg-ui",
        },
        "games": [
            {"key": "n64/usa.zelda", "attrs": ["n64.usa.zelda"], "reasons": ["build"]},
            {"key": "switch/world.totk", "attrs": [], "reasons": ["extras"]},
        ],
    }
    data.update(overrides)
    return json.dumps(data)


def test_the_answer_is_read_whole():
    r = parse(report())
    assert r is not None
    assert (r.available, r.behind, r.unbuilt, r.writable, r.stale, r.pending) == (True, True, False, True, False, False)
    assert r.picker == "/nix/store/x-gotg-ui"
    assert set(r.games) == {("n64", "usa.zelda"), ("switch", "world.totk")}
    assert r.games[("n64", "usa.zelda")].attrs == ("n64.usa.zelda",)
    assert r.games[("switch", "world.totk")].reasons == ("extras",)


def test_nothing_garbage_and_a_version_not_spoken_all_say_nothing():
    assert parse("") is None
    assert parse("not json") is None
    assert parse("[]") is None
    assert parse(report(version=2)) is None
    assert parse(report(version="1")) is None
    # True == 1 in Python; a boolean is not the version.
    assert parse(report(version=True)) is None
    assert parse(report()[:-5]) is None


def test_a_bare_string_where_a_list_belongs_is_not_spelled_out_letter_by_letter():
    r = parse(report(games=[{"key": "n64/usa.x", "attrs": "xy", "reasons": "build"}]))
    assert r is not None
    assert r.games == {}
    r = parse(report(games=[{"key": "n64/usa.x", "attrs": "xy", "reasons": ["build", 7]}]))
    assert r is not None
    assert r.games[("n64", "usa.x")].attrs == ()
    assert r.games[("n64", "usa.x")].reasons == ("build",)


def test_a_game_with_a_bad_key_or_no_reasons_is_left_out():
    r = parse(report(games=[{"key": "nonsense", "reasons": ["build"]}, {"key": "n64/usa.x", "reasons": []}]))
    assert r is not None
    assert r.games == {}


def test_outdated_is_what_the_report_names_among_what_is_here():
    r = parse(report())
    assert outdated(r, {("n64", "usa.zelda"), ("snes", "world.metroid")}) == {("n64", "usa.zelda")}
    assert outdated(None, {("n64", "usa.zelda")}) == frozenset()


def test_the_badge_precedence_ring_then_alert_then_arrow():
    key = ("n64", "usa.zelda")
    assert badge(key, {key}, {key}, {key}) == RING
    assert badge(key, {key}, {key}, set()) == ALERT
    assert badge(key, {key}, set(), set()) == INSTALLED
    assert badge(key, set(), set(), set()) == NONE


def test_the_chip_truth_table():
    from gotg_ui.updates import AFTER_INSTALLS, RESTART_TO_UPDATE, UPDATE_AVAILABLE, UPDATE_FAILED, UPDATING, chip

    behind = parse(report())
    gotg = {"available": False, "behind": False, "unbuilt": False, "picker": "/nix/store/x-gotg-ui"}
    current = parse(report(gotg={**gotg, "picker_current": True}))
    assert chip(behind, "/nix/store/x-gotg-ui") == UPDATE_AVAILABLE
    assert chip(current, "/nix/store/x-gotg-ui") is None
    # The root holds the picker the lock wants, and this is not it: a restart.
    assert chip(current, "/nix/store/old-gotg-ui") == RESTART_TO_UPDATE
    # The root holds some other picker -- older, say -- and the lock does not
    # vouch for it: no restart offered into it.
    assert chip(parse(report(gotg=gotg)), "/nix/store/newer-gotg-ui") is None
    # The dev shell: no self, so never "restart", but "available" still shows.
    assert chip(behind, None) == UPDATE_AVAILABLE
    assert chip(current, None) is None
    # Nothing to say, and nowhere to write a pin.
    assert chip(None, "/nix/store/x-gotg-ui") is None
    assert chip(parse(report(writable=False)), "/nix/store/x-gotg-ui") is None
    # The loop's own word wins; one it does not know is nothing.
    assert chip(behind, None, phase="updating") == UPDATING
    assert chip(None, None, phase="failed") == UPDATE_FAILED
    assert chip(behind, None, phase="busy") == AFTER_INSTALLS
    assert chip(behind, None, phase="nonsense") is None


def stub(tmp_path, body: str, monkeypatch) -> None:
    path = tmp_path / "gotg"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GOTG_BIN", str(path))


def test_ask_runs_complete_updates_and_reads_the_report(tmp_path, monkeypatch):
    stub(tmp_path, f"echo \"$@\" > {tmp_path}/argv; cat <<'EOF'\n{report()}\nEOF", monkeypatch)
    r = ask()
    assert (tmp_path / "argv").read_text().split() == ["complete", "updates"]
    assert r is not None and r.behind


def test_an_older_client_that_prints_nothing_and_a_failing_one_both_say_nothing(tmp_path, monkeypatch):
    stub(tmp_path, "exit 0", monkeypatch)
    assert ask() is None
    stub(tmp_path, "echo boom >&2; exit 1", monkeypatch)
    assert ask() is None
    monkeypatch.setenv("GOTG_BIN", str(tmp_path / "missing"))
    assert ask() is None
