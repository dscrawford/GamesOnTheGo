"""The shelf: a list down the left, the art of the one in hand on the right.

Steam's library, in other words — one game per row, and the picture big enough
to look at. Geometry, like the grid's, and for the same reason: the failures
are the ones a screenshot on one screen size will not show. The strip along the
top is the case that has already gone wrong once here.
"""

import pytest

from gotg_ui.layout import LIST_FRACTION, SHELF_PER_PAGE, SHELF_ROWS, shelf, shelf_at

SIZES = [(1280, 800), (1280, 720), (1920, 1080), (2560, 1440), (800, 600)]
STRIP = 54


@pytest.mark.parametrize("size", SIZES)
def test_every_row_is_on_the_screen(size):
    width, height = size
    _hero, rows = shelf(width, height, STRIP)
    assert len(rows) == SHELF_PER_PAGE
    for row in rows:
        assert row.x >= 0
        assert row.y >= STRIP
        assert row.x + row.width <= width
        assert row.y + row.height <= height


@pytest.mark.parametrize("size", SIZES)
def test_nothing_is_drawn_under_the_strip(size):
    hero, rows = shelf(*size, STRIP)
    assert hero.y >= STRIP
    assert min(row.y for row in rows) >= STRIP


@pytest.mark.parametrize("size", SIZES)
def test_the_rows_are_a_list_not_a_grid(size):
    # One game per line, each the full width of the column: stacked, never
    # side by side.
    _hero, rows = shelf(*size, STRIP)
    for above, below in zip(rows, rows[1:], strict=False):
        assert above.x == below.x
        assert above.width == below.width
        assert above.y + above.height <= below.y


@pytest.mark.parametrize("size", SIZES)
def test_a_row_is_wider_than_it_is_tall(size):
    # It holds an icon and a title beside it. A tall row would be a tile.
    _hero, rows = shelf(*size, STRIP)
    assert rows[0].width > rows[0].height * 2


@pytest.mark.parametrize("size", SIZES)
def test_the_art_is_to_the_right_of_the_list(size):
    # Not above it, which is what this view was first built as and is not what
    # was asked for.
    hero, rows = shelf(*size, STRIP)
    assert hero.x >= rows[0].x + rows[0].width


@pytest.mark.parametrize("size", SIZES)
def test_the_art_keeps_a_covers_shape(size):
    hero, _rows = shelf(*size, STRIP)
    assert hero.height / hero.width == pytest.approx(1.5, abs=0.06)


@pytest.mark.parametrize("size", SIZES)
def test_the_art_is_the_biggest_thing_on_screen(size):
    # The point of the view: the picture, large.
    hero, rows = shelf(*size, STRIP)
    assert hero.height > rows[0].height * 4


@pytest.mark.parametrize("size", SIZES)
def test_the_list_gets_about_the_share_it_was_given(size):
    width, _height = size
    _hero, rows = shelf(*size, STRIP)
    assert rows[0].width / width == pytest.approx(LIST_FRACTION, abs=0.08)


def test_reading_order_is_top_to_bottom():
    # Index and position are the same number, so the cursor needs no
    # arithmetic to find out where it is.
    _hero, rows = shelf(1280, 800, STRIP)
    assert rows[0].y < rows[1].y < rows[-1].y
    assert all(row.x == rows[0].x for row in rows)


def test_a_point_lands_on_the_row_it_looks_like():
    _hero, rows = shelf(1280, 800, STRIP)
    for index in (0, 5, SHELF_ROWS - 1):
        row = rows[index]
        assert shelf_at(row.x + 2, row.y + 2, 1280, 800, STRIP) == index


def test_the_art_side_is_not_a_row():
    # Clicking the picture must not select whatever row is level with it.
    hero, _rows = shelf(1280, 800, STRIP)
    assert shelf_at(hero.x + hero.width // 2, hero.y + hero.height // 2, 1280, 800, STRIP) is None


def test_the_gaps_are_not_rows():
    _hero, rows = shelf(1280, 800, STRIP)
    between = rows[0].y + rows[0].height + 1
    if between < rows[1].y:
        assert shelf_at(rows[0].x + 2, between, 1280, 800, STRIP) is None
    assert shelf_at(1, 1, 1280, 800, STRIP) is None


def test_it_shows_more_games_than_the_grid():
    # The reason to have it: more of the library legible at once.
    from gotg_ui.layout import PER_PAGE

    assert SHELF_PER_PAGE > PER_PAGE


def test_a_window_too_small_to_be_sensible_still_produces_rectangles():
    hero, rows = shelf(200, 150, STRIP)
    assert hero.width >= 1 and hero.height >= 1
    assert all(row.width >= 1 and row.height >= 1 for row in rows)
