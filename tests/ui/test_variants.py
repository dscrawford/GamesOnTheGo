"""The other ways one game can be run, read off the client's env files.

A variant is a file rather than a catalog row, so the only thing to get right
is which files count — and the rule has to be the client's, or the menu offers
a row `gotg play` will refuse.
"""

from __future__ import annotations

import pytest

from gotg_ui.catalog import Game
from gotg_ui.variants import disabled_for, env_dir, variants_for


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


def client_saying(tmp_path, monkeypatch, *disabled):
    """A stand-in `gotg complete disabled`, naming the mods it rules out."""
    log = tmp_path / "argv"
    script = tmp_path / "gotg"
    lines = "".join(f"echo {name}\n" for name in disabled)
    script.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >>{log}\n{lines}')
    script.chmod(0o755)
    monkeypatch.setenv("GOTG_BIN", str(script))
    return log


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("GOTG_ENV_DIR", raising=False)
    monkeypatch.delenv("GOTG_ROOT", raising=False)
    monkeypatch.setenv("GOTG_UI_ENV", str(tmp_path))
    # A client that rules nothing out, so the tests below are about the files.
    client_saying(tmp_path, monkeypatch)
    (tmp_path / "games" / "n64").mkdir(parents=True)
    return tmp_path / "games" / "n64"


def test_a_game_with_no_variants_has_none(env):
    (env / "usa.zelda.nix").touch()
    assert variants_for(game()) == ()


def test_variants_are_the_second_component_of_the_file_name(env):
    for name in ("usa.zelda.rando.nix", "usa.zelda.hd.nix", "usa.zelda.nix"):
        (env / name).touch()
    assert variants_for(game()) == ("hd", "rando")


def test_another_game_is_not_this_game(env):
    # A prefix match would make usa.zelda_2's variants show up under usa.zelda.
    (env / "usa.zelda_2.rando.nix").touch()
    (env / "usa.zelda.rando.nix").touch()
    assert variants_for(game()) == ("rando",)


def test_a_name_the_client_would_refuse_is_not_offered(env):
    # env.sh accepts ^[a-z0-9][a-z0-9_-]*$ and nothing else; a row that cannot
    # be played is worse than no row.
    for name in ("usa.zelda.two.dots.nix", "usa.zelda.Caps.nix", "usa.zelda.-leading.nix", "usa.zelda.ok.nix"):
        (env / name).touch()
    assert variants_for(game()) == ("ok",)


def test_a_platform_with_no_directory_is_quiet(env):
    assert variants_for(game(platform="dreamcast")) == ()


def test_no_client_beside_us_means_no_variants(monkeypatch):
    # The picker run from a checkout: the grid still draws, the menu is what it
    # always was.
    for key in ("GOTG_UI_ENV", "GOTG_ENV_DIR", "GOTG_ROOT"):
        monkeypatch.delenv(key, raising=False)
    assert env_dir() is None
    assert variants_for(game()) == ()


def test_the_client_wrapper_names_the_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("GOTG_UI_ENV", raising=False)
    monkeypatch.delenv("GOTG_ENV_DIR", raising=False)
    monkeypatch.setenv("GOTG_ROOT", str(tmp_path))
    assert env_dir() == tmp_path / "env"


# --- and the ones no version installed here can run -------------------------


def test_a_mod_the_client_rules_out_is_not_offered(env, tmp_path, monkeypatch):
    # UltraCam on Tears of the Kingdom 1.4.3: the mod patches nothing it
    # recognises and the game dies a minute in, so there is no launch behind
    # the row and the row goes.
    for name in ("usa.zelda.rando.nix", "usa.zelda.ultracam.nix"):
        (env / name).touch()
    client_saying(tmp_path, monkeypatch, "ultracam")
    assert variants_for(game()) == ("rando",)


def test_the_client_is_asked_about_the_game_it_is_drawing(env, tmp_path, monkeypatch):
    (env / "usa.zelda.rando.nix").touch()
    log = client_saying(tmp_path, monkeypatch)
    variants_for(game())
    assert log.read_text().strip() == "complete disabled n64/usa.zelda"


def test_a_game_with_no_variants_never_asks(env, tmp_path, monkeypatch):
    # Which is nearly every game: a subprocess per menu open, for a list that
    # can only be empty, is a cost with nothing on the other side.
    log = client_saying(tmp_path, monkeypatch)
    assert variants_for(game()) == ()
    assert not log.exists()


def test_a_client_that_cannot_answer_rules_nothing_out(env, monkeypatch, tmp_path):
    # Hiding a working mod because a subprocess failed is the worse half of
    # the trade: the launch still refuses in words.
    (env / "usa.zelda.rando.nix").touch()
    monkeypatch.setenv("GOTG_BIN", str(tmp_path / "not-here"))
    assert disabled_for(game()) == frozenset()
    assert variants_for(game()) == ("rando",)
