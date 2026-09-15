"""The action menu a pick opens: what can be done with one game.

Picking used to mean playing; now it means choosing. The verbs are what the
client can do with a game from here — play it, open its emulator's own
settings, put it in Steam — and the panel sits beside the tile on whichever
side has room.

A game with variants gets one more row, at the top, naming which of them the
verbs below will act on. Opening that row replaces the list with the variants
and choosing one comes back, so the mods are a menu somebody walks into rather
than a mode they have to know about — and "Add to Steam" on a mod is the same
row it always was, which is the point of putting the choice above the verbs
instead of beside them.
"""

from __future__ import annotations

from collections.abc import Sequence

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

# The row that opens the variants, and the one that comes back from them.
# Their verbs never leave this program: the menu handles both itself.
MODS = "mods"
BACK = "mods-back"

# What the plain game is called in a list of mods. Not "none": the row is a
# thing to choose, and every other row in that list is a name.
PLAIN = "the game as it shipped"


class Menu:
    """Open over one tile, holding which verb the cursor is on."""

    def __init__(
        self,
        game: Game,
        tile_index: int,
        installed: bool = False,
        variants: tuple[str, ...] | Sequence[str] = (),
    ):
        self.game = game
        self.tile_index = tile_index
        self.installed = installed
        self.variants = tuple(variants)
        # Which variant the verbs act on; None is the game as it shipped.
        self.variant: str | None = None
        self.expanded = False
        self.selected = 0

    @property
    def actions(self) -> list[tuple[str, str]]:
        """The rows as they stand: the verbs, or the variants while open."""
        if self.expanded:
            rows = [("Back", BACK), (PLAIN, "variant:")]
            return rows + [(name, f"variant:{name}") for name in self.variants]

        verbs = [*ACTIONS, UNINSTALL] if self.installed else list(ACTIONS)
        if not self.variants:
            return verbs
        # First, because it decides what every row under it means.
        return [(f"Mods: {self.variant or PLAIN}", MODS), *verbs]

    def open_variants(self) -> None:
        self.expanded = True
        # On whichever is current, so a second visit starts where the first
        # left off rather than at the top.
        names = [None, *self.variants]
        self.selected = names.index(self.variant) + 1 if self.variant in names else 1

    def close_variants(self) -> None:
        """Back out of the variant list without choosing one."""
        self.expanded = False
        self.selected = 0

    def choose(self, verb: str) -> None:
        """Take a `variant:` row. The menu stays open and goes back to the
        verbs, because choosing a mod is not doing anything to it yet."""
        self.variant = verb[len("variant:") :] or None
        self.expanded = False
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

    @property
    def picks_a_variant(self) -> bool:
        """Whether confirming this row stays inside the menu."""
        return self.action == MODS or self.action == BACK or self.action.startswith("variant:")

    def confirm(self) -> str | None:
        """Take the current row. Returns the verb for the client to run, or
        None when the row was about the menu itself."""
        verb = self.action
        if verb == MODS:
            self.open_variants()
            return None
        if verb == BACK:
            self.close_variants()
            return None
        if verb.startswith("variant:"):
            self.choose(verb)
            return None
        return verb

    def move(self, delta: int) -> None:
        self.selected = max(0, min(len(self.actions) - 1, self.selected + delta))

    def select(self, index: int) -> bool:
        """The pointer's way in. False for a row that is not there."""
        if not 0 <= index < len(self.actions):
            return False
        self.selected = index
        return True
