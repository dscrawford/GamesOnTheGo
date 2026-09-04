"""The action menu a pick opens: what can be done with one game.

Picking used to mean playing; now it means choosing. The three verbs are the
three things the client can do with a game from here — play it, open its
emulator's own settings, put it in Steam — and the panel sits beside the tile
on whichever side has room.
"""

from __future__ import annotations

from .catalog import Game
from .layout import COLUMNS

# Label and verb. The verb is what reaches the client — `gotg <verb>` — except
# steam-add, which is two words and mapped where the command is built, and
# controllers and storage, which are screens in this program and never leave it.
#
# The controller diagram is also on C, but a handheld has no keyboard and this
# is the only way to it there — which is the case it was written for.
ACTIONS: list[tuple[str, str]] = [
    ("Play Game", "play"),
    ("Configure", "configure"),
    ("Controllers", "controllers"),
    ("Storage", "storage"),
    ("Add to Steam", "steam-add"),
]
# Only offered for a game that is here: uninstalling nothing is not a verb,
# and a row that does nothing is a row somebody will press.
UNINSTALL: tuple[str, str] = ("Uninstall", "uninstall")


class Menu:
    """Open over one tile, holding which verb the cursor is on."""

    def __init__(self, game: Game, tile_index: int, installed: bool = False):
        self.game = game
        self.tile_index = tile_index
        self.actions = [*ACTIONS, UNINSTALL] if installed else list(ACTIONS)
        self.selected = 0

    @property
    def side(self) -> str:
        """Where the panel goes: beside the tile, on the side with room.

        Decided by column alone so both rows agree — a tile in the right
        columns would push a right-hand panel off screen.
        """
        return "right" if (self.tile_index % COLUMNS) < COLUMNS - 2 else "left"

    @property
    def action(self) -> str:
        return self.actions[self.selected][1]

    def move(self, delta: int) -> None:
        self.selected = max(0, min(len(self.actions) - 1, self.selected + delta))

    def select(self, index: int) -> bool:
        """The pointer's way in. False for a row that is not there."""
        if not 0 <= index < len(self.actions):
            return False
        self.selected = index
        return True
