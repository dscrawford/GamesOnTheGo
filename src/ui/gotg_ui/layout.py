"""Where the ten tiles go.

Geometry only, and no pygame: this is the half that can be wrong in a way a
screenshot will not show — a tile two pixels off the edge on one screen size
and not another — so it is the half worth holding in a test.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config

COLUMNS = int(config.get("theme.grid.columns", 5))
ROWS = int(config.get("theme.grid.rows", 2))
PER_PAGE = COLUMNS * ROWS

# 600x900 is what SteamGridDB calls a portrait grid, so a tile is 2:3. A tile
# of any other shape either letterboxes the art or stretches it.
TILE_ASPECT = float(config.get("theme.grid.tile_aspect", 900 / 600))

# Fractions of the shorter axis, so the grid keeps its proportions from a
# 1280x800 Deck to a 4K television without a second set of numbers.
GAP_FRACTION = float(config.get("theme.grid.gap_fraction", 0.02))
MARGIN_FRACTION = float(config.get("theme.grid.margin_fraction", 0.04))


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    width: int
    height: int

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """What pygame wants."""
        return (self.x, self.y, self.width, self.height)


def grid(width: int, height: int, reserved: int = 0) -> list[Tile]:
    """Ten tiles, in reading order, centred in the window.

    `reserved` is a band along the top that belongs to something else -- the
    strip saying who is holding which controller -- and the tiles are centred
    in what is left rather than drawn under it. In screen coordinates
    throughout, so a pointer lands on the tile it looks like it landed on.

    Reading order because the selection index and the position on the page are
    the same number — index 4 is top-right, 5 is the start of the second row —
    and anything else makes the input handler do arithmetic to find out where
    the cursor is.

    Sized to whichever axis runs out first, so a wide window gets bars at the
    sides rather than tiles cropped off the bottom.
    """
    reserved = max(0, min(reserved, height - 1))
    height = height - reserved
    short = max(1, min(width, height))
    gap = max(1, int(short * GAP_FRACTION))
    margin = max(1, int(short * MARGIN_FRACTION))

    usable_width = max(1, width - 2 * margin - gap * (COLUMNS - 1))
    usable_height = max(1, height - 2 * margin - gap * (ROWS - 1))

    tile_width = usable_width // COLUMNS
    # The height the width implies, and the width the height implies; the
    # smaller pair is the one that fits.
    if tile_width * TILE_ASPECT * ROWS > usable_height:
        tile_height = usable_height // ROWS
        tile_width = int(tile_height / TILE_ASPECT)
    else:
        tile_height = int(tile_width * TILE_ASPECT)

    tile_width = max(1, tile_width)
    tile_height = max(1, tile_height)

    span_x = tile_width * COLUMNS + gap * (COLUMNS - 1)
    span_y = tile_height * ROWS + gap * (ROWS - 1)
    left = (width - span_x) // 2
    top = reserved + (height - span_y) // 2

    return [
        Tile(
            x=left + column * (tile_width + gap),
            y=top + row * (tile_height + gap),
            width=tile_width,
            height=tile_height,
        )
        for row in range(ROWS)
        for column in range(COLUMNS)
    ]


def tile_at(x: int, y: int, width: int, height: int, reserved: int = 0) -> int | None:
    """Which tile a point is on, or None for the gaps and the margins.

    Tested against the rectangles rather than derived from the arithmetic, so
    it cannot drift out of step with grid(). The gaps answering None is the
    part that matters: a pointer resting between two tiles must not quietly
    mean the one beside it, because a click that looks like it missed would
    then launch a game.
    """
    for index, tile in enumerate(grid(width, height, reserved)):
        if tile.x <= x < tile.x + tile.width and tile.y <= y < tile.y + tile.height:
            return index
    return None


# The shelf: the other way to look at the same library. Many small covers in
# rows, with the one under the cursor shown full size beside them.
SHELF_COLUMNS = int(config.get("theme.shelf.columns", 8))
SHELF_ROWS = int(config.get("theme.shelf.rows", 3))
SHELF_PER_PAGE = SHELF_COLUMNS * SHELF_ROWS

# How much of the window the full art gets. Half, so the art is the thing being
# looked at and the rows are what is being looked *through*.
HERO_FRACTION = float(config.get("theme.shelf.hero_fraction", 0.5))


def shelf(width: int, height: int, reserved: int = 0) -> tuple[Tile, list[Tile]]:
    """The full art, and the rows of covers under it.

    Same rules as `grid`: reading order, centred, sized to whichever axis runs
    out first. The hero keeps a cover's own 2:3 rather than filling its band,
    because art stretched to fit a box is the one thing this view exists to
    avoid.
    """
    reserved = max(0, min(reserved, height - 1))
    height = height - reserved
    short = max(1, min(width, height))
    gap = max(1, int(short * GAP_FRACTION))
    margin = max(1, int(short * MARGIN_FRACTION))

    hero_band = max(1, int(height * HERO_FRACTION))
    hero_height = max(1, hero_band - 2 * margin)
    hero_width = max(1, int(hero_height / TILE_ASPECT))
    if hero_width > width - 2 * margin:
        hero_width = max(1, width - 2 * margin)
        hero_height = max(1, int(hero_width * TILE_ASPECT))
    # Left, not centred: a cover is 2:3, so a centred one in a band this shape
    # leaves a third of the window empty on each side. Against the margin there
    # is room beside it for the title of the thing being looked at.
    hero = Tile(
        x=margin * 2,
        y=reserved + margin + (hero_band - 2 * margin - hero_height) // 2,
        width=hero_width,
        height=hero_height,
    )

    rows_band = max(1, height - hero_band)
    usable_width = max(1, width - 2 * margin - gap * (SHELF_COLUMNS - 1))
    usable_height = max(1, rows_band - 2 * margin - gap * (SHELF_ROWS - 1))

    tile_width = usable_width // SHELF_COLUMNS
    if tile_width * TILE_ASPECT * SHELF_ROWS > usable_height:
        tile_height = usable_height // SHELF_ROWS
        tile_width = int(tile_height / TILE_ASPECT)
    else:
        tile_height = int(tile_width * TILE_ASPECT)
    tile_width = max(1, tile_width)
    tile_height = max(1, tile_height)

    span_x = tile_width * SHELF_COLUMNS + gap * (SHELF_COLUMNS - 1)
    span_y = tile_height * SHELF_ROWS + gap * (SHELF_ROWS - 1)
    left = (width - span_x) // 2
    top = reserved + hero_band + (rows_band - span_y) // 2

    tiles = [
        Tile(
            x=left + column * (tile_width + gap),
            y=top + row * (tile_height + gap),
            width=tile_width,
            height=tile_height,
        )
        for row in range(SHELF_ROWS)
        for column in range(SHELF_COLUMNS)
    ]
    return hero, tiles


def shelf_at(x: int, y: int, width: int, height: int, reserved: int = 0) -> int | None:
    """Which cover a point is on, or None. Against the rectangles, as
    `tile_at` is, so the pointer and the drawing cannot drift apart."""
    _hero, tiles = shelf(width, height, reserved)
    for index, tile in enumerate(tiles):
        if tile.x <= x < tile.x + tile.width and tile.y <= y < tile.y + tile.height:
            return index
    return None
