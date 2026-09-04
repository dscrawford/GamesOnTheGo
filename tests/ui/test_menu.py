"""The action menu a pick opens: three verbs beside the tile.

No pygame here — the cases worth holding are which side the panel lands on,
what confirming means, and that the cursor arithmetic never names a verb that
is not there.
"""

from __future__ import annotations

import pytest

from gotg_ui.catalog import Game
from gotg_ui.menu import ACTIONS, Menu


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


def test_the_verbs_in_order():
    assert [label for label, _ in ACTIONS] == ["Play Game", "Configure", "Controllers", "Add to Steam"]


def test_opening_holds_the_game_and_starts_on_play():
    m = Menu(game(), tile_index=0)
    assert m.game.id == "usa.zelda"
    assert m.selected == 0
    assert m.action == "play"


def test_moving_walks_and_clamps():
    m = Menu(game(), tile_index=0)
    m.move(1)
    assert m.action == "configure"
    m.move(1)
    assert m.action == "controllers"
    m.move(1)
    assert m.action == "steam-add"
    m.move(1)
    assert m.action == "steam-add", "clamped at the ends, not wrapped"
    m.move(-5)
    assert m.action == "play"


@pytest.mark.parametrize(
    ("tile_index", "side"),
    [(0, "right"), (1, "right"), (2, "right"), (3, "left"), (4, "left"),
     (5, "right"), (7, "right"), (8, "left"), (9, "left")],
)
def test_the_panel_lands_on_the_open_side_of_the_tile(tile_index, side):
    # A tile in the right columns would push its panel off screen; the panel
    # goes where the room is, decided by column alone so both rows agree.
    assert Menu(game(), tile_index=tile_index).side == side


def test_selecting_an_item_by_index_is_refused_off_the_list():
    m = Menu(game(), tile_index=0)
    assert m.select(3) is True
    assert m.action == "steam-add"
    assert m.select(4) is False
    assert m.action == "steam-add", "a refused select leaves the cursor"
    assert m.select(-1) is False


# --- uninstall, only for a game that is here ------------------------------------


def test_a_game_that_is_here_gets_uninstall_last():
    m = Menu(game(), tile_index=0, installed=True)
    assert [label for label, _ in m.actions] == ["Play Game", "Configure", "Controllers", "Add to Steam", "Uninstall"]
    m.move(10)
    assert m.action == "uninstall"


def test_a_game_that_is_not_here_has_no_uninstall_to_press():
    m = Menu(game(), tile_index=0, installed=False)
    assert len(m.actions) == 4
    m.move(10)
    assert m.action == "steam-add"
    assert m.select(4) is False
