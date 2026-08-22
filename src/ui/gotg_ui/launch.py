"""Handing a game to the client.

exec rather than spawn-and-wait, so the process Steam is watching becomes the
game: the overlay, the per-game controller layout and the playtime all attach
to it rather than to a picker sitting in front of it. The cost is that quitting
the emulator does not come back here, which is the trade docs/ui-plan.md takes.

Nothing here knows how a game runs. `gotg play` already resolves the
environment, builds it, downloads what is missing and pulls the saves, and all
of that is covered by the client's own suite.
"""

from __future__ import annotations

import os

from .catalog import Game


class LaunchError(Exception):
    """The client could not be started."""


def gotg_bin() -> str:
    """The client. On PATH from the wrapper, overridable for a test.

    An empty override is treated as unset: `GOTG_BIN=` in an environment file
    should fall back rather than try to exec the empty string.
    """
    return os.environ.get("GOTG_BIN") or "gotg"


def command_for(game: Game, verb: str = "play") -> list[str]:
    """`gotg <verb> <platform>/<id>`.

    Qualified, always. Ids are unique per platform and not globally — the
    README's own example is snes/usa.bugs_life — and the grid is the one thing
    that knows which of them you were looking at when you pressed A. Passing a
    bare id throws that away and asks the client to guess.
    """
    return [gotg_bin(), verb, f"{game.platform}/{game.id}"]


def play(game: Game, verb: str = "play") -> None:
    """Replace this process with the client's verb — the game, or its
    emulator's settings screen. Returns only on failure."""
    command = command_for(game, verb)
    try:
        os.execvp(command[0], command)
    except OSError as error:
        # execvp raises before it replaces anything, so this is reachable, and
        # it is the failure somebody will actually hit: the UI installed on a
        # machine where the client is not.
        raise LaunchError(f"could not start {command[0]}: {error}") from error
