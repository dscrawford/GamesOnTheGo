"""Where the ten tiles go.

Geometry only, and no pygame: this is the half that can be wrong in a way a
screenshot will not show — a tile two pixels off the edge on one screen size
and not another — so it is the half worth holding in a test.
"""

from __future__ import annotations

from dataclasses import dataclass

COLUMNS = 5
ROWS = 2
PER_PAGE = COLUMNS * ROWS

# 600x900 is what SteamGridDB calls a portrait grid, so a tile is 2:3. A tile
# of any other shape either letterboxes the art or stretches it.
TILE_ASPECT = 900 / 600

# Fractions of the shorter axis, so the grid keeps its proportions from a
# 1280x800 Deck to a 4K television without a second set of numbers.
GAP_FRACTION = 0.02
MARGIN_FRACTION = 0.04


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


def grid(width: int, height: int) -> list[Tile]:
    """Ten tiles, in reading order, centred in the window.

    Reading order because the selection index and the position on the page are
    the same number — index 4 is top-right, 5 is the start of the second row —
    and anything else makes the input handler do arithmetic to find out where
    the cursor is.

    Sized to whichever axis runs out first, so a wide window gets bars at the
    sides rather than tiles cropped off the bottom.
    """
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
    top = (height - span_y) // 2

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
