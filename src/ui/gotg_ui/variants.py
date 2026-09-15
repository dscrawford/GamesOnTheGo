"""The other ways one game can be run.

A variant is a second environment for the same game — a mod, a port, a frame
rate, a four-player split screen — and the client reaches it as
`gotg play <id> <variant>`. They are files rather than catalog rows
(`env/games/<platform>/<id>.<variant>.nix`), because what a mod *is* is a
build, and the catalog knows only about bytes on a server.

So this reads the directory, which is what the client does. Not
`gotg complete variants`: that filters the whole catalog through jq to find the
platform this program already knows, and the grid asks the moment a menu opens.

The one thing the directory cannot answer is whether a mod can run: a mod
states the game versions it was built against, and with nothing here inside
that window there is no launch it could do. The client is asked for those by
name — a short list, usually empty — and they are left out, because a row that
answers with a paragraph is a row somebody presses twice.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .catalog import Game
from .launch import gotg_bin

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


# The client walks one game's install directory and reads built manifests; a
# hung client must not hold a menu closed.
DISABLED_TIMEOUT = 3


def disabled_for(game: Game) -> frozenset[str]:
    """Which of this game's variants no version installed here can run.

    Silent on failure, and empty for a client too old to know the subcommand:
    the menu then offers what it always offered, and the launch still refuses
    in words. Hiding a working mod because a subprocess failed would be the
    worse half of the trade.
    """
    command = [gotg_bin(), "complete", "disabled", f"{game.platform}/{game.id}"]
    try:
        done = subprocess.run(command, capture_output=True, timeout=DISABLED_TIMEOUT, text=True)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if done.returncode != 0:
        return frozenset()
    return frozenset(line.strip() for line in done.stdout.splitlines() if line.strip())


def variants_for(game: Game, where: Path | None = None) -> tuple[str, ...]:
    """Every variant of one game that can run, sorted, or nothing at all.

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
    names = {name for name in names if NAME.match(name)}
    if names:
        names -= disabled_for(game)
    return tuple(sorted(names))
