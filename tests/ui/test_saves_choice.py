"""Choosing between two saves before a game starts: reading the client's
answer, and which card is lit."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from gotg_ui.catalog import Game
from gotg_ui.saves_choice import HERE, REMOTE, Check, Choice, Side, parse, when

CONFLICT = {
    "attr": "env-n64",
    "state": "conflict",
    "here": {"device": "daniel-deck", "updated": "2026-09-20T10:00:00Z", "generation": 3, "changed": True},
    "remote": {"device": "daniel-desktop", "updated": "2026-09-21T08:00:00Z", "generation": 5, "size": 10},
}


def game() -> Game:
    return Game(id="usa.donkey_kong_64", platform="n64", title="Donkey Kong 64", handler="single_file")


def test_a_conflict_is_read_with_both_machines_and_dates():
    found = parse(json.dumps(CONFLICT))
    assert found is not None and found.conflict
    assert found.here == Side("daniel-deck", "2026-09-20T10:00:00Z")
    assert found.remote == Side("daniel-desktop", "2026-09-21T08:00:00Z")


def test_the_last_json_line_is_the_answer_and_noise_is_not():
    # The client narrates on stderr, but a stray line on stdout must not
    # make a launch unreadable.
    assert parse("resolving...\n" + json.dumps(CONFLICT)).conflict
    assert parse("not json") is None
    assert parse(json.dumps({"state": "same"})) is None, "no here: not an answer"


def test_anything_but_a_conflict_starts_the_game():
    for state in ("same", "remote-newer", "here-newer", "here-only", "remote-only", "offline"):
        found = parse(json.dumps({**CONFLICT, "state": state}))
        assert found is not None and not found.conflict, state
    assert parse(json.dumps({**CONFLICT, "remote": None})).remote is None


def test_the_newer_save_is_lit_first_and_left_right_move_between_them():
    found = parse(json.dumps(CONFLICT))
    choice = Choice.open(game(), None, None, found)
    assert choice.selected == REMOTE, "the service's is newer"
    assert choice.move(-1).selected == HERE
    assert choice.move(-1).move(1).selected == REMOTE
    assert choice.move(0) == choice
    older_remote = Check("conflict", Side("deck", "2026-09-22T00:00:00Z"), Side("desk", "2026-09-21T00:00:00Z"))
    assert Choice.open(game(), None, None, older_remote).selected == HERE


def test_a_date_reads_as_a_person_would_say_it():
    now = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
    local = lambda iso: when(iso, now)  # noqa: E731
    assert local("").startswith("unknown")
    assert local("2026-09-28T20:00:00Z").startswith(("today", "yesterday"))
    assert "Sep" in local("2026-09-02T10:00:00Z")
    assert "2024" in local("2024-03-01T10:00:00Z")
    assert local("garbage") == "garbage"
