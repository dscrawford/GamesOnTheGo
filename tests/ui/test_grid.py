"""Moving the cursor and turning pages.

Grid holds no pygame, so the awkward cases reach a test even though the drawing
does not: a short last page, a filter that empties the screen, a stick held
against the edge.
"""

from __future__ import annotations

import pytest

from gotg_ui.catalog import Game, Library
from gotg_ui.grid import Grid


def library(count, platform="snes"):
    return Library(
        [Game(id=f"usa.g{i:03}", platform=platform, title=f"G{i}", handler="single_file") for i in range(count)]
    )


def test_the_cursor_starts_top_left():
    state = Grid(library(25))
    assert (state.page_index, state.selected) == (0, 0)
    assert state.game.id == "usa.g000"


def test_moving_right_walks_the_row():
    state = Grid(library(25))
    for expected in range(1, 5):
        state.move(1, 0)
        assert state.selected == expected


def test_down_and_up_change_row_and_keep_the_column():
    state = Grid(library(25))
    state.move(2, 0)
    state.move(0, 1)
    assert state.selected == 7, "second row, same column"
    state.move(0, -1)
    assert state.selected == 2


def test_right_off_the_last_column_carries_to_the_next_page():
    # Paging is the shoulders' job, but a stick that dead-ends at the right
    # makes somebody press them for something the stick should have done.
    state = Grid(library(25))
    state.selected = 4
    state.move(1, 0)
    assert state.page_index == 1
    assert state.selected == 0


def test_left_off_the_first_column_carries_back():
    state = Grid(library(25))
    state.turn(1)
    state.selected = 0
    state.move(-1, 0)
    assert state.page_index == 0
    assert state.selected == 4


def test_the_first_page_does_not_carry_off_the_front():
    state = Grid(library(25))
    state.move(-1, 0)
    assert (state.page_index, state.selected) == (0, 0)


def test_the_last_page_does_not_carry_off_the_end():
    state = Grid(library(25))
    state.turn(1)
    state.turn(1)
    assert state.page_index == 2
    state.selected = len(state.page) - 1
    state.move(1, 0)
    assert state.page_index == 2, "nowhere to go, and not an error"


def test_the_cursor_never_lands_past_a_short_last_page():
    # 25 games is a last page of five, and a cursor at index 9 there would name
    # a game that is not on screen.
    state = Grid(library(25))
    state.selected = 9
    state.turn(1)
    state.turn(1)
    assert state.selected < len(state.page)
    assert state.game is not None


@pytest.mark.parametrize("dx,dy", [(1, 0), (-1, 0), (0, 1), (0, -1)])
def test_moving_on_a_short_page_stays_on_a_real_game(dx, dy):
    state = Grid(library(12))  # a last page of two
    state.turn(1)
    for _ in range(6):
        state.move(dx, dy)
        assert state.game is not None
        assert state.selected < len(state.page)


def test_an_empty_library_moves_nowhere_and_names_no_game():
    # What a filter matching nothing leaves behind. It must not raise.
    state = Grid(library(0))
    assert state.page == []
    assert state.game is None
    state.move(1, 0)
    state.move(0, 1)
    assert state.turn(1) is False
    assert state.game is None


def test_turning_past_either_end_is_refused_not_clamped_silently():
    state = Grid(library(25))
    assert state.turn(-1) is False
    assert state.page_index == 0
    assert state.turn(1) is True
    assert state.turn(1) is True
    assert state.turn(1) is False
    assert state.page_index == 2


def test_one_page_exactly_has_nowhere_to_turn():
    state = Grid(library(10))
    assert state.library.pages == 1
    assert state.turn(1) is False
    assert state.turn(-1) is False


# --- pointing at one directly -------------------------------------------------


def test_selecting_a_tile_by_index():
    state = Grid(library(25))
    assert state.select(7) is True
    assert state.selected == 7
    assert state.game.id == "usa.g007"


@pytest.mark.parametrize("index", [-1, 10, 99])
def test_selecting_off_the_page_is_refused_and_changes_nothing(index):
    state = Grid(library(25))
    state.select(3)
    assert state.select(index) is False
    assert state.selected == 3, "a refused select leaves the cursor where it was"


def test_selecting_past_a_short_last_page_is_refused():
    # 25 games is a last page of five: tiles 5..9 are drawn empty, and a click
    # on one of them names no game.
    state = Grid(library(25))
    state.turn(1)
    state.turn(1)
    assert state.select(4) is True
    assert state.select(5) is False
    assert state.game is not None


def test_selecting_on_an_empty_grid_is_refused():
    state = Grid(library(0))
    assert state.select(0) is False
    assert state.game is None


# --- a list is one sequence --------------------------------------------------


def _list_grid(count=30, rows=12):
    from gotg_ui.catalog import Game, Library
    from gotg_ui.grid import Grid

    games = [Game(id=f"usa.g{i:02d}", platform="n64", title=f"G{i}", handler="rom") for i in range(count)]
    return Grid(Library(games, rows), columns=1, rows=rows)


def test_a_list_carries_to_the_next_page_going_down():
    # Steam's library scrolls; it does not stop at the twelfth line and wait
    # for somebody to find the page buttons.
    state = _list_grid()
    for _ in range(13):
        state.move(0, 1)
    assert state.page_index == 1
    assert state.game.id == "usa.g13"


def test_a_list_carries_back_up():
    state = _list_grid()
    for _ in range(13):
        state.move(0, 1)
    for _ in range(13):
        state.move(0, -1)
    assert state.page_index == 0
    assert state.game.id == "usa.g00"


def test_a_list_stops_at_the_end_of_the_library():
    state = _list_grid()
    for _ in range(100):
        state.move(0, 1)
    assert state.game.id == "usa.g29"


def test_a_short_last_page_does_not_leave_the_cursor_past_the_end():
    # 30 games in pages of 12 is a last page of six.
    state = _list_grid()
    for _ in range(100):
        state.move(0, 1)
    assert state.selected < len(state.page)


def test_a_grid_still_clamps_downwards():
    # The row below the bottom one is nothing, and paging there would move the
    # cursor two places for one press.
    from gotg_ui.catalog import Game, Library
    from gotg_ui.grid import Grid

    games = [Game(id=f"usa.g{i:02d}", platform="n64", title=f"G{i}", handler="rom") for i in range(30)]
    state = Grid(Library(games, 10), columns=5, rows=2)
    for _ in range(9):
        state.move(0, 1)
    assert state.page_index == 0
