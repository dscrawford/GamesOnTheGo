"""Property under test: one answer to "who owns the input", and the same
answer says what is drawn -- plus the grid's pad presses by name."""

from __future__ import annotations

import itertools

from gotg_ui import buttons
from gotg_ui.screens import Screen, grid_press, screen_of

OFF = dict(preparing=False, saves_check=False, saves=False, storage=False, menu=False, panel=False, typing=False)


def of(**on) -> Screen:
    return screen_of(**{**OFF, **on})


def test_nothing_open_is_the_grid():
    assert of() is Screen.GRID


def test_each_screen_alone():
    assert of(preparing=True) is Screen.PREPARE
    assert of(saves_check=True) is Screen.SAVES_CHECK
    assert of(saves=True) is Screen.SAVES
    assert of(storage=True) is Screen.STORAGE
    assert of(menu=True) is Screen.MENU
    assert of(panel=True) is Screen.PANEL
    assert of(typing=True) is Screen.TYPING


def test_the_filter_panel_keeps_the_keyboard_it_raised_but_stays_drawn():
    assert of(panel=True, typing=True) is Screen.PANEL_TYPING


def test_the_order_is_the_one_the_input_chain_had():
    order = ["preparing", "saves_check", "saves", "storage", "menu", "panel", "typing"]
    wanted = [
        Screen.PREPARE, Screen.SAVES_CHECK, Screen.SAVES, Screen.STORAGE, Screen.MENU, Screen.PANEL, Screen.TYPING,
    ]
    for i in range(len(order)):
        # Everything below `name` is open too; `name` still wins.
        assert of(**dict.fromkeys(order[i:], True)) in (wanted[i], Screen.PANEL_TYPING)


def test_every_combination_has_an_answer():
    for combo in itertools.product([False, True], repeat=len(OFF)):
        assert isinstance(screen_of(**dict(zip(OFF, combo, strict=True))), Screen)


def test_grid_presses_by_name():
    assert grid_press(buttons.A) == "menu"
    assert grid_press(buttons.B) == "quit"
    assert grid_press(buttons.LB) == "page-back"
    assert grid_press(buttons.RB) == "page-forward"
    assert grid_press(buttons.Y) == "platform"
    assert grid_press(buttons.X) == "search"
    assert grid_press(buttons.START) == "panel"
    assert grid_press(buttons.BACK) == "update"
    assert grid_press("nonsense") is None
    assert grid_press(None) is None
