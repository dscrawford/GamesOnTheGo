"""Where the ten tiles go.

Kept away from pygame on purpose: geometry is the half that can be wrong in a
way a screenshot will not show, and it is the half a test can hold.
"""

from __future__ import annotations

import pytest

from gotg_ui.layout import COLUMNS, ROWS, TILE_ASPECT, grid, tile_at

DECK = (1280, 800)  # the screen this is for
DESKTOP = (1920, 1080)


def test_ten_tiles_five_across_and_two_down():
    assert (COLUMNS, ROWS) == (5, 2)
    tiles = grid(*DECK)
    assert len(tiles) == 10
    assert len({t.y for t in tiles}) == 2
    assert len({t.x for t in tiles}) == 5


@pytest.mark.parametrize("size", [DECK, DESKTOP, (800, 600), (3840, 2160), (640, 480)])
def test_every_tile_lands_on_screen(size):
    width, height = size
    for tile in grid(width, height):
        assert tile.x >= 0 and tile.y >= 0
        assert tile.x + tile.width <= width
        assert tile.y + tile.height <= height


@pytest.mark.parametrize("size", [DECK, DESKTOP, (800, 600), (3840, 2160)])
def test_tiles_never_overlap(size):
    tiles = grid(*size)
    for i, a in enumerate(tiles):
        for b in tiles[i + 1 :]:
            apart = a.x + a.width <= b.x or b.x + b.width <= a.x or a.y + a.height <= b.y or b.y + b.height <= a.y
            assert apart, f"{a} overlaps {b}"


@pytest.mark.parametrize("size", [DECK, DESKTOP, (800, 600), (3840, 2160)])
def test_a_tile_keeps_the_box_art_shape(size):
    # 600x900 is what SteamGridDB calls a portrait grid, and a tile that is not
    # 2:3 either letterboxes the art or stretches it.
    for tile in grid(*size):
        assert tile.height == pytest.approx(tile.width * TILE_ASPECT, rel=0.02)


@pytest.mark.parametrize("size", [DECK, DESKTOP, (800, 600)])
def test_the_grid_is_centred(size):
    width, height = size
    tiles = grid(width, height)
    left = min(t.x for t in tiles)
    right = max(t.x + t.width for t in tiles)
    top = min(t.y for t in tiles)
    bottom = max(t.y + t.height for t in tiles)
    assert left == pytest.approx(width - right, abs=2)
    assert top == pytest.approx(height - bottom, abs=2)


def test_tiles_come_back_in_reading_order():
    # Index 0 is top-left and 4 is top-right, because the selection index is
    # the same number as the position in the page.
    tiles = grid(*DECK)
    first_row, second_row = tiles[:5], tiles[5:]
    assert [t.x for t in first_row] == sorted(t.x for t in first_row)
    assert all(a.y == first_row[0].y for a in first_row)
    assert second_row[0].y > first_row[0].y


def test_a_window_too_small_for_a_grid_still_returns_ten_usable_tiles():
    # A tiny window is a person dragging a corner, not a reason to divide by
    # zero or hand back rectangles with no area.
    for tile in grid(200, 120):
        assert tile.width > 0 and tile.height > 0


# --- pointing at a tile -------------------------------------------------------


def test_the_centre_of_every_tile_finds_that_tile():
    tiles = grid(*DECK)
    for index, tile in enumerate(tiles):
        x = tile.x + tile.width // 2
        y = tile.y + tile.height // 2
        assert tile_at(x, y, *DECK) == index


@pytest.mark.parametrize("size", [DECK, DESKTOP, (800, 600)])
def test_every_corner_inside_a_tile_still_finds_it(size):
    for index, tile in enumerate(grid(*size)):
        corners = [
            (tile.x, tile.y),
            (tile.x + tile.width - 1, tile.y),
            (tile.x, tile.y + tile.height - 1),
            (tile.x + tile.width - 1, tile.y + tile.height - 1),
        ]
        for x, y in corners:
            assert tile_at(x, y, *size) == index


def test_the_gaps_between_tiles_are_nothing():
    # A pointer resting in a gap must not silently mean the tile beside it, or
    # a click that looks like it missed would launch a game.
    tiles = grid(*DECK)
    between = (tiles[0].x + tiles[0].width + tiles[1].x) // 2
    assert tile_at(between, tiles[0].y + 5, *DECK) is None

    below_first_row = tiles[0].y + tiles[0].height + 2
    assert tile_at(tiles[0].x + 5, below_first_row, *DECK) is None


@pytest.mark.parametrize(
    "point",
    [(0, 0), (5, 5), (1279, 799), (640, 795), (-10, -10), (5000, 5000)],
    ids=["origin", "margin", "far-corner", "status-line", "negative", "way-outside"],
)
def test_pointing_outside_every_tile_is_nothing(point):
    assert tile_at(point[0], point[1], *DECK) is None


def test_a_tiny_window_does_not_claim_the_whole_screen():
    # grid() floors tiles at one pixel; tile_at must not then answer for every
    # point in a window that has no room for a grid.
    assert tile_at(199, 119, 200, 120) in (None, *range(10))
