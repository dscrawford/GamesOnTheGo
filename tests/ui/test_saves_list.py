"""Going back to a save from the picker: reading the client's list, which
games have one, and the screen's walk from a list to a prompt to a restore."""

from __future__ import annotations

import json

from gotg_ui.catalog import Game
from gotg_ui.saves_list import Listing, Saves, has_own_saves, parse

LIST = {
    "attr": "env-n64-usa_donkey_kong_64",
    "dedicated": True,
    "offline": False,
    "saves": [
        {
            "id": "remote:7",
            "source": "remote",
            "generation": 7,
            "hash": "a" * 64,
            "device": "daniel-deck",
            "written_at": "2026-09-30T21:10:00Z",
            "size": 100,
            "here": True,
        },
        {
            "id": "local:2026-09-29T080000Z-bbbbbbbbbbbb.tar.zst",
            "source": "local",
            "generation": None,
            "hash": "b" * 64,
            "device": "daniel-desktop",
            "written_at": "2026-09-29T08:00:00Z",
            "size": 90,
            "here": False,
        },
        {
            "id": "remote:6",
            "source": "remote",
            "generation": 6,
            "hash": "c" * 64,
            "device": "",
            "written_at": "2026-09-28T19:00:00Z",
            "size": 80,
            "here": False,
        },
    ],
}


def game() -> Game:
    return Game(id="usa.donkey_kong_64", platform="n64", title="Donkey Kong 64", handler="single_file")


def listing() -> Listing:
    found = parse(json.dumps(LIST))
    assert found is not None
    return found


# --- reading the client -----------------------------------------------------


def test_the_list_is_read_in_the_clients_order():
    found = listing()
    assert [entry.id for entry in found.entries] == [
        "remote:7",
        "local:2026-09-29T080000Z-bbbbbbbbbbbb.tar.zst",
        "remote:6",
    ]
    assert found.entries[0].here and not found.entries[1].here
    assert found.entries[1].source == "local"
    assert found.dedicated and not found.offline


def test_log_lines_before_the_answer_are_not_the_answer():
    found = parse("dk64 runs in env-n64-usa_donkey_kong_64\n" + json.dumps(LIST) + "\n")
    assert found is not None and len(found.entries) == 3


def test_an_answer_that_is_not_a_list_is_none():
    assert parse("") is None
    assert parse("not json") is None
    assert parse(json.dumps({"attr": "env-n64"})) is None


def test_an_entry_with_an_id_restore_would_refuse_is_dropped():
    # The id goes back to the client as an argument; anything not shaped
    # like one of its two kinds never becomes a row that can be pressed.
    odd = dict(LIST, saves=[dict(LIST["saves"][0], id="--force"), LIST["saves"][2]])
    assert [entry.id for entry in parse(json.dumps(odd)).entries] == ["remote:6"]


# --- which games have a Saves row -------------------------------------------


def test_a_game_with_its_own_environment_has_saves(tmp_path):
    (tmp_path / "games" / "n64").mkdir(parents=True)
    (tmp_path / "games" / "n64" / "usa.donkey_kong_64.nix").write_text("{}")
    assert has_own_saves(game(), None, where=tmp_path)


def test_a_game_on_its_platforms_environment_has_none(tmp_path):
    # env-n64 holds every N64 game's saves: going back would rewind them all.
    (tmp_path / "games" / "n64").mkdir(parents=True)
    assert not has_own_saves(game(), None, where=tmp_path)


def test_a_mod_is_its_own_environment(tmp_path):
    (tmp_path / "games" / "n64").mkdir(parents=True)
    (tmp_path / "games" / "n64" / "usa.donkey_kong_64.rando.nix").write_text("{}")
    assert has_own_saves(game(), "rando", where=tmp_path)
    assert not has_own_saves(game(), None, where=tmp_path)


def test_emulate_is_the_platforms_environment(tmp_path):
    (tmp_path / "games" / "n64").mkdir(parents=True)
    (tmp_path / "games" / "n64" / "usa.donkey_kong_64.nix").write_text("{}")
    assert not has_own_saves(game(), "emulate", where=tmp_path)


def test_no_client_beside_it_means_no_saves_row():
    assert not has_own_saves(game(), None, where=None)


# --- the screen ---------------------------------------------------------------


def test_it_opens_loading_and_lands_on_the_newest():
    screen = Saves.open(game(), None, None)
    assert screen.listing is None and not screen.failed
    screen = screen.loaded(listing())
    assert screen.selected == 0
    assert screen.entry.id == "remote:7"


def test_moving_walks_the_list_and_clamps():
    screen = Saves.open(game(), None, None).loaded(listing())
    screen = screen.move(1).move(1).move(1)
    assert screen.entry.id == "remote:6"
    assert screen.move(-5).entry.id == "remote:7"


def test_a_is_a_prompt_first_and_a_restore_second():
    screen = Saves.open(game(), None, None).loaded(listing()).move(1)
    screen, restore = screen.press_a()
    assert screen.confirming and restore is None
    # The prompt holds the cursor where it was: up and down do nothing.
    assert screen.move(1).entry.id == screen.entry.id
    screen, restore = screen.press_a()
    assert restore is not None and restore.id == "local:2026-09-29T080000Z-bbbbbbbbbbbb.tar.zst"


def test_b_backs_out_of_the_prompt_then_the_screen():
    screen = Saves.open(game(), None, None).loaded(listing())
    screen, _ = screen.press_a()
    screen = screen.press_b()
    assert screen is not None and not screen.confirming
    assert screen.press_b() is None


def test_nothing_to_press_while_loading_or_empty():
    screen = Saves.open(game(), None, None)
    assert screen.press_a() == (screen, None)
    empty = screen.loaded(Listing(dedicated=True, offline=False, entries=()))
    assert empty.press_a() == (empty, None)
    assert empty.entry is None


def test_a_list_that_could_not_be_had_says_so():
    screen = Saves.open(game(), None, None).loaded(None)
    assert screen.failed and screen.entry is None


def test_the_rows_say_when_and_where_and_which_is_now():
    screen = Saves.open(game(), None, None).loaded(listing())
    now, archived, unknown = (screen.describe(entry) for entry in screen.listing.entries)
    assert "daniel-deck" in now and "now" in now.lower()
    assert "daniel-desktop" in archived and "archive" in archived.lower()
    assert "unknown machine" in unknown.lower()
