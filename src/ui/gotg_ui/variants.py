"""The other ways one game can be run.

A variant is a second environment for the same game — a mod, a port, a frame
rate, a four-player split screen — and the client reaches it as
`gotg play <id> <variant>`. They are files rather than catalog rows
(`env/games/<platform>/<id>.<variant>.nix`), because what a mod *is* is a
build, and the catalog knows only about bytes on a server.

So this reads the directory, which is what the client does. Not
`gotg complete variants`: that filters the whole catalog through jq to find the
platform this program already knows, and the grid asks the moment a menu opens.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .catalog import Game

# What the client will accept after an id — env.sh's own rule. A file with a
# stray second dot makes a name `gotg play` rejects, and offering it here would
# be a menu row that cannot work.
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def env_dir() -> Path | None:
    """The client's environment files, which the wrapper points at. None where
    this is running from a checkout with no client beside it — then a game
    simply has no variants, and the menu is what it always was."""
    for key in ("GOTG_UI_ENV", "GOTG_ENV_DIR"):
        found = os.environ.get(key)
        if found:
            return Path(found)
    root = os.environ.get("GOTG_ROOT")
    return Path(root) / "env" if root else None


def variants_for(game: Game, where: Path | None = None) -> tuple[str, ...]:
    """Every variant of one game, sorted, or nothing at all.

    Sorted rather than in directory order: the list is a menu somebody learns
    the shape of, and a filesystem's order is not stable between machines.
    """
    where = where if where is not None else env_dir()
    if where is None:
        return ()
    try:
        files = list((where / "games" / game.platform).glob(f"{game.id}.*.nix"))
    except OSError:
        return ()
    names = {file.name[len(game.id) + 1 : -len(".nix")] for file in files}
    return tuple(sorted(name for name in names if NAME.match(name)))
