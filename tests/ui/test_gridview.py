"""Property under test: the words under a game and in the status corner, and the
one bundle of what a frame draws -- all without pygame."""

from __future__ import annotations

import dataclasses

import pytest

from gotg_ui.catalog import Game, Library
from gotg_ui.grid import Grid
from gotg_ui.gridview import GridView, row_under_text, status_line


def game(platform="snes"):
    return Game(id="usa.g", platform=platform, title="G", handler="single_file")


def state(count):
    return Grid(Library([Game(id=f"usa.g{i:03}", platform="snes", title=f"G{i}", handler="x") for i in range(count)]))


# --- the line under a row's title ---------------------------------------------


def test_a_game_that_is_neither_installed_nor_installing_is_its_platform():
    assert row_under_text(game("n64"), None, False, False) == "n64"


def test_installed_says_so():
    assert row_under_text(game(), None, True, False) == "snes   ·   installed"


def test_installed_and_outdated_says_there_is_an_update():
    assert row_under_text(game(), None, True, True) == "snes   ·   installed  ·  update available"


def test_outdated_alone_means_nothing_for_a_game_that_is_not_here():
    assert row_under_text(game(), None, False, True) == "snes"


def test_an_install_in_flight_outranks_installed_and_outdated():
    assert row_under_text(game(), (0.4, False), True, True) == "snes   ·   installing"
    assert row_under_text(game(), (None, False), False, False) == "snes   ·   installing"


def test_a_failed_install_says_so():
    assert row_under_text(game(), (None, True), False, False) == "snes   ·   install failed"


# --- the status corner ---------------------------------------------------------


def test_the_status_the_browser_gave_is_shown_dim():
    assert status_line(state(25), "3 installed", None) == ("3 installed", False)


def test_with_no_status_it_says_which_page():
    assert status_line(state(25), "", None) == ("page 1 of 3  ·  25 games", False)


def test_an_empty_catalog_says_so():
    assert status_line(state(0), "", None) == ("no games in the catalog", False)


def test_typing_replaces_the_status_and_is_bright():
    assert status_line(state(25), "3 installed", "mar") == ("search: mar_", True)
    assert status_line(state(25), "", "") == ("search: _", True)


# --- the bundle ----------------------------------------------------------------


def test_a_view_is_what_a_frame_draws_and_cannot_be_changed():
    view = GridView(state=state(3))
    assert view.status == "" and view.art is None and view.chip is None
    assert view.installed is None and view.rings is None and view.outdated is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.status = "x"


def test_a_view_is_rebuilt_with_replace():
    view = GridView(state=state(3), status="a")
    assert dataclasses.replace(view, status="b").status == "b"
    assert view.status == "a"
