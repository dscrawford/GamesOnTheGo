"""The action menu a pick opens: three verbs beside the tile.

No pygame here — the cases worth holding are which side the panel lands on,
what confirming means, and that the cursor arithmetic never names a verb that
is not there.
"""

from __future__ import annotations

import pytest

from gotg_ui.catalog import Game
from gotg_ui.menu import ACTIONS, MODS, PLAIN, Menu


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


def test_the_verbs_in_order():
    assert [label for label, _ in ACTIONS] == ["Play Game", "Configure", "Controllers", "Storage", "Add to Steam"]


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
    assert m.action == "storage"
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
    assert m.select(4) is True
    assert m.action == "steam-add"
    assert m.select(5) is False
    assert m.action == "steam-add", "a refused select leaves the cursor"
    assert m.select(-1) is False


# --- uninstall, only for a game that is here ------------------------------------


def test_a_game_that_is_here_gets_uninstall_last():
    m = Menu(game(), tile_index=0, installed=True)
    assert [label for label, _ in m.actions] == [
        "Play Game",
        "Configure",
        "Controllers",
        "Storage",
        "Add to Steam",
        "Uninstall",
    ]
    m.move(10)
    assert m.action == "uninstall"


def test_a_game_that_is_not_here_has_no_uninstall_to_press():
    m = Menu(game(), tile_index=0, installed=False)
    assert len(m.actions) == 5
    m.move(10)
    assert m.action == "steam-add"
    assert m.select(5) is False


# --- mods, as a menu you walk into ------------------------------------------


def with_mods(installed=False):
    return Menu(game(), tile_index=0, installed=installed, variants=("2p", "3p", "4p"))


def test_a_game_with_no_variants_is_the_menu_it_always_was():
    assert [label for label, _ in Menu(game(), 0).actions] == [label for label, _ in ACTIONS]


def test_the_mods_row_comes_first_and_names_what_is_chosen():
    # First, because it decides what every row under it means.
    m = with_mods()
    label, verb = m.actions[0]
    assert verb == MODS and label == f"Mods: {PLAIN}"
    assert [v for _, v in m.actions[1:]] == [v for _, v in ACTIONS]


def test_opening_the_row_replaces_the_verbs_with_the_variants():
    m = with_mods()
    assert m.confirm() is None, "opening the list does nothing to the game"
    assert [label for label, _ in m.actions] == ["Back", PLAIN, "2p", "3p", "4p"]


def test_choosing_a_variant_comes_back_to_the_verbs():
    m = with_mods()
    m.confirm()
    m.select(3)  # 4p: Back, the plain game, 2p, 3p...
    assert m.confirm() is None, "choosing a mod is not doing anything to it yet"
    assert m.variant == "3p"
    assert m.actions[0][0] == "Mods: 3p"
    assert m.selected == 0, "back at the top, on a verb"


def test_the_plain_game_can_be_chosen_back():
    m = with_mods()
    m.confirm()
    m.select(2)
    m.confirm()
    assert m.variant == "2p"
    m.confirm()  # the mods row again
    m.select(1)  # the game as it shipped
    m.confirm()
    assert m.variant is None


def test_a_second_visit_starts_on_the_current_variant():
    m = with_mods()
    m.confirm()
    m.select(4)
    m.confirm()
    assert m.variant == "4p"
    m.confirm()
    assert m.action == "variant:4p", "the list opens where it was left"


def test_backing_out_of_the_list_keeps_the_variant():
    m = with_mods()
    m.confirm()
    m.select(2)
    m.confirm()
    m.confirm()  # open it again
    m.select(0)  # Back
    assert m.confirm() is None
    assert m.variant == "2p" and not m.expanded


def test_a_verb_is_what_leaves_the_menu():
    m = with_mods()
    m.confirm()
    m.select(2)
    m.confirm()
    m.select(1)  # Play Game, under the mods row
    assert m.confirm() == "play"
    assert m.variant == "2p", "and it carries the mod with it"


def test_every_verb_still_reachable_with_a_mod_chosen():
    m = with_mods(installed=True)
    verbs = [verb for _, verb in m.actions]
    assert verbs == [MODS, "play", "configure", "controllers", "storage", "steam-add", "uninstall"]


def test_the_cursor_never_names_a_row_that_is_not_there():
    m = with_mods()
    for _ in range(20):
        m.move(1)
    assert m.action == m.actions[-1][1]
    for _ in range(20):
        m.move(-1)
    m.confirm()  # into the list, from the mods row at the top
    for _ in range(20):
        m.move(1)
    assert m.action == "variant:4p", "the last variant, not past it"


def test_uninstall_is_about_the_game_rather_than_a_mod():
    # `gotg uninstall` takes the game's bytes and every launcher with them, so
    # the row means the same thing whichever mod is chosen — the app does not
    # hand it one.
    m = with_mods(installed=True)
    m.confirm()
    m.select(2)
    m.confirm()
    assert m.variant == "2p"
    m.selected = len(m.actions) - 1
    assert m.confirm() == "uninstall"
