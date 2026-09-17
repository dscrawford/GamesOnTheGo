"""Which versions of a game are installed, asked of the client.

A Switch game is a base and a pile of updates, and which update runs decides
what the game is: a mod built against one executable does nothing on another,
or crashes minutes in. So the picker offers the choice the same way it offers
mods — and asks the client rather than reading the disk itself, because
"installed" and "what a mod can take" are the client's to define and it
already answers both.

Filesystem-only on the client's side, so the grid can afford to ask when a
menu opens.
"""

from __future__ import annotations

import subprocess

from .catalog import Game
from .launch import gotg_bin
from .recent import Recent

# The client walks one game's install directory; a hung client must not hold a
# menu closed.
VERSIONS_TIMEOUT = 3


# What the client already answered, by game and variant.
#
# Opening a menu spawns `gotg complete versions`, measured at 31 ms on a Steam
# Deck -- two dropped frames, on the press that opens the menu, which is
# exactly where a stall is felt. The answer only changes when something is
# installed or removed, and both go through this program.
_answers = Recent(128)


def forget() -> None:
    """Drop what was remembered. Called when an install or an uninstall makes
    it wrong, which is the only thing that can."""
    _answers.clear()


def versions_for(game: Game, variant: str | None = None) -> tuple[tuple[str, bool], ...]:
    """Every installed version, newest first, each with whether it is the one
    a launch would run. Empty for a game with no updates — which is most of
    them, and means the menu shows no version row at all.

    Silent on failure: an older client without the subcommand answers exit 0
    with no output, read the same way as a game with nothing to choose.
    """
    remembered = _answers.get((game.key, variant))
    if remembered is not None:
        return remembered

    command = [gotg_bin(), "complete", "versions", f"{game.platform}/{game.id}"]
    if variant:
        command.append(variant)
    try:
        done = subprocess.run(command, capture_output=True, timeout=VERSIONS_TIMEOUT, text=True)
    except (OSError, subprocess.SubprocessError):
        # Not remembered: a client that was busy or missing may answer next
        # time, and caching the silence would make one bad moment permanent.
        return ()
    if done.returncode != 0:
        return ()

    found = []
    for line in done.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        # The client marks the one a launch would run with a leading star.
        running = name.startswith("*")
        found.append((name.lstrip("*"), running))
    return _answers.put((game.key, variant), tuple(found))


def names(rows: tuple[tuple[str, bool], ...]) -> tuple[str, ...]:
    return tuple(name for name, _ in rows)


def running(rows: tuple[tuple[str, bool], ...]) -> str | None:
    """The one the client would pick on its own, or None when it would refuse
    — which is what a mod with no version old enough looks like."""
    for name, is_running in rows:
        if is_running:
            return name
    return None
