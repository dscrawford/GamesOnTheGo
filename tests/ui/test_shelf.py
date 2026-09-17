"""The shelf: rows of covers with the one under the cursor shown full size.

Geometry, like the grid's, and for the same reason — the failures are the ones
a screenshot on one screen size will not show. The strip along the top is the
case that has already gone wrong once here: a view that centres in the window
rather than in what is left of it draws its first row under the seats.
"""

import pytest

from gotg_ui.layout import SHELF_COLUMNS, SHELF_PER_PAGE, SHELF_ROWS, shelf, shelf_at

SIZES = [(1280, 800), (1280, 720), (1920, 1080), (2560, 1440), (800, 600)]
STRIP = 54


@pytest.mark.parametrize("size", SIZES)
def test_every_cover_is_on_the_screen(size):
    width, height = size
    _hero, tiles = shelf(width, height, STRIP)
    assert len(tiles) == SHELF_PER_PAGE
    for tile in tiles:
        assert tile.x >= 0
        assert tile.y >= STRIP
        assert tile.x + tile.width <= width
        assert tile.y + tile.height <= height


@pytest.mark.parametrize("size", SIZES)
def test_nothing_is_drawn_under_the_strip(size):
    # The band along the top belongs to the seats. A view that ignored it would
    # put its first row behind them.
    hero, tiles = shelf(*size, STRIP)
    assert hero.y >= STRIP
    assert min(tile.y for tile in tiles) >= STRIP


@pytest.mark.parametrize("size", SIZES)
def test_the_covers_keep_a_covers_shape(size):
    # 2:3, because that is what the art is. Anything else letterboxes or
    # stretches it, and stretching is what this view exists to avoid.
    _hero, tiles = shelf(*size, STRIP)
    assert tiles[0].height / tiles[0].width == pytest.approx(1.5, abs=0.06)


@pytest.mark.parametrize("size", SIZES)
def test_the_full_art_keeps_it_too(size):
    hero, _tiles = shelf(*size, STRIP)
    assert hero.height / hero.width == pytest.approx(1.5, abs=0.06)


@pytest.mark.parametrize("size", SIZES)
def test_the_art_is_bigger_than_the_covers(size):
    # The whole point of the view: the one under the cursor is the one you can
    # actually see.
    hero, tiles = shelf(*size, STRIP)
    assert hero.width > tiles[0].width * 2


@pytest.mark.parametrize("size", SIZES)
def test_the_rows_are_below_the_art(size):
    hero, tiles = shelf(*size, STRIP)
    assert min(tile.y for tile in tiles) >= hero.y + hero.height - 1


@pytest.mark.parametrize("size", SIZES)
def test_the_covers_do_not_overlap(size):
    _hero, tiles = shelf(*size, STRIP)
    for a, b in zip(tiles, tiles[1:], strict=False):
        same_row = a.y == b.y
        if same_row:
            assert a.x + a.width <= b.x


def test_reading_order():
    # Index and position are the same number, so the cursor needs no arithmetic
    # to find out where it is.
    _hero, tiles = shelf(1280, 800, STRIP)
    assert tiles[0].y == tiles[SHELF_COLUMNS - 1].y
    assert tiles[SHELF_COLUMNS].y > tiles[0].y
    assert tiles[0].x < tiles[1].x


def test_a_point_lands_on_the_cover_it_looks_like():
    _hero, tiles = shelf(1280, 800, STRIP)
    for index in (0, 5, SHELF_PER_PAGE - 1):
        tile = tiles[index]
        assert shelf_at(tile.x + 2, tile.y + 2, 1280, 800, STRIP) == index


def test_the_gaps_are_not_covers():
    # A click that looks like it missed must not launch a game.
    _hero, tiles = shelf(1280, 800, STRIP)
    between = tiles[0].x + tiles[0].width + 1
    if between < tiles[1].x:
        assert shelf_at(between, tiles[0].y + 2, 1280, 800, STRIP) is None
    assert shelf_at(1, 1, 1280, 800, STRIP) is None


def test_it_holds_more_than_the_grid():
    # The reason to have it: more of the library on screen at once.
    from gotg_ui.layout import PER_PAGE

    assert SHELF_PER_PAGE > PER_PAGE
    assert SHELF_COLUMNS * SHELF_ROWS == SHELF_PER_PAGE


def test_a_window_too_small_to_be_sensible_still_produces_rectangles():
    # Nothing here may divide by zero or hand back a negative width, whatever
    # somebody resizes to.
    hero, tiles = shelf(200, 150, STRIP)
    assert hero.width >= 1 and hero.height >= 1
    assert all(tile.width >= 1 and tile.height >= 1 for tile in tiles)
