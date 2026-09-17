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

# The rows that open a list, and the one that comes back from them. Their
# verbs never leave this program: the menu handles them itself.
MODS = "mods"
VERSIONS = "versions"
BACK = "back"

# What the plain game is called in a list of mods. Not "none": the row is a
# thing to choose, and every other row in that list is a name.
PLAIN = "vanilla"

# What "let the client decide" is called in a list of versions — the newest
# installed, or the newest a chosen mod was built for. It is the right answer
# often enough to be the default, and naming it beats an empty row.
AUTOMATIC = "automatic"


class Menu:
    """Open over one tile, holding which verb the cursor is on."""

    def __init__(
        self,
        game: Game,
        tile_index: int,
        installed: bool = False,
        variants: tuple[str, ...] | Sequence[str] = (),
        versions: tuple[str, ...] | Sequence[str] = (),
        columns: int = COLUMNS,
    ):
        self.game = game
        self.tile_index = tile_index
        # The shape the menu was opened in. The grid's five by default, so
        # every existing caller is unchanged; the shelf passes its one.
        self.columns = max(1, columns)
        self.installed = installed
        self.variants = tuple(variants)
        # Newest first, as the client lists them — a version list reads like a
        # changelog, and the one somebody wants is usually at the top.
        self.versions = tuple(versions)
        # Which variant the verbs act on; None is vanilla.
        self.variant: str | None = None
        # Which version; None leaves the choice to the client, which knows
        # what a mod can take.
        self.version: str | None = None
        # Which list is open, if any.
        self.expanded: str | None = None
        self.selected = 0

    @property
    def actions(self) -> list[tuple[str, str]]:
        """The rows as they stand: the verbs, or whichever list is open."""
        if self.expanded == MODS:
            rows = [("Back", BACK), (PLAIN, "variant:")]
            return rows + [(name, f"variant:{name}") for name in self.variants]
        if self.expanded == VERSIONS:
            rows = [("Back", BACK), (AUTOMATIC, "version:")]
            return rows + [(name, f"version:{name}") for name in self.versions]

        verbs = [*ACTIONS, UNINSTALL] if self.installed else list(ACTIONS)
        # Above the verbs, because they decide what every row under them means.
        # A game with one version has nothing to choose, so it gets no row.
        chooser = []
        if self.variants:
            chooser.append((f"Mods: {self.variant or PLAIN}", MODS))
        if len(self.versions) > 1:
            chooser.append((f"Version: {self.version or AUTOMATIC}", VERSIONS))
        return chooser + verbs

    def open_list(self, which: str) -> None:
        self.expanded = which
        # On whichever is current, so a second visit starts where the first
        # left off rather than at the top.
        if which == MODS:
            names = [None, *self.variants]
            current = self.variant
        else:
            names = [None, *self.versions]
            current = self.version
        self.selected = names.index(current) + 1 if current in names else 1

    def close_list(self) -> None:
        """Back out of an open list without choosing from it."""
        self.expanded = None
        self.selected = 0

    def choose(self, verb: str) -> None:
        """Take a `variant:` or `version:` row. The menu stays open and goes
        back to the verbs, because choosing is not yet doing."""
        if verb.startswith("variant:"):
            self.variant = verb[len("variant:") :] or None
        else:
            self.version = verb[len("version:") :] or None
        self.expanded = None
        self.selected = 0

    @property
    def side(self) -> str:
        """Where the panel goes: beside the tile, on the side with room.

        Decided by column alone so both rows agree — a tile in the right
        columns would push a right-hand panel off screen. By *this menu's*
        columns, not the grid module's: the shelf is one column wide, and
        asking the grid's five about it gives an answer about a shape that is
        not on screen.
        """
        if self.columns == 1:
            # A list lives against the left edge and the whole right-hand side
            # is the art, so there is only one side with room.
            return "right"
        return "right" if (self.tile_index % self.columns) < self.columns - 2 else "left"

    @property
    def action(self) -> str:
        return self.actions[self.selected][1]

    def confirm(self) -> str | None:
        """Take the current row. Returns the verb for the client to run, or
        None when the row was about the menu itself."""
        verb = self.action
        if verb in (MODS, VERSIONS):
            self.open_list(verb)
            return None
        if verb == BACK:
            self.close_list()
            return None
        if verb.startswith(("variant:", "version:")):
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
