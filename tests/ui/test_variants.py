"""The other ways one game can be run, read off the client's env files.

A variant is a file rather than a catalog row, so the only thing to get right
is which files count — and the rule has to be the client's, or the menu offers
a row `gotg play` will refuse.
"""

from __future__ import annotations

import pytest

from gotg_ui.catalog import Game
from gotg_ui.variants import env_dir, variants_for


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("GOTG_ENV_DIR", raising=False)
    monkeypatch.delenv("GOTG_ROOT", raising=False)
    monkeypatch.setenv("GOTG_UI_ENV", str(tmp_path))
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
