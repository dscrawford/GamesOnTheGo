"""The picker's colours and window size, from config/theme.yaml with defaults.

One table for every drawing module (it was written out in app.py and again in
saves_draw.py). The window is the Deck's own panel, so a window on a desktop is
the shape it will be there.
"""

from __future__ import annotations

from . import config

BACKGROUND = config.colour("theme.colours.background", (18, 18, 20))
TILE = config.colour("theme.colours.tile", (38, 38, 44))
TILE_SELECTED = config.colour("theme.colours.tile_selected", (58, 104, 148))
TEXT = config.colour("theme.colours.text", (232, 232, 236))
TEXT_DIM = config.colour("theme.colours.text_dim", (150, 150, 158))
ATTENTION = config.colour("theme.colours.attention", (232, 176, 64))
# The menu's list, a shade off the background.
PANEL = config.colour("theme.colours.panel", (26, 26, 30))

# The Deck's own panel, so a window on a desktop is the shape it will be there.
WINDOW = tuple(config.get("theme.window", [1280, 800]))
