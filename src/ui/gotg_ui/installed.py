"""Which games are here, asked of the client.

The client owns what "installed" means — a member file, a recipe's directory,
an id with whatever extension the recipe chose — and `gotg list` marks rows by
it. Asking `gotg complete installed` keeps one definition; a second one here
would be the copy that drifts. Filesystem-only on the client's side, so it is
cheap enough to ask at startup and again after an uninstall.
"""

from __future__ import annotations

import subprocess

from .launch import gotg_bin

# Walks the whole catalog once against the disk; ~5000 rows measured well
# under a second, and a hung client must not hold the window closed.
INSTALLED_TIMEOUT = 5


def installed_games() -> set[tuple[str, str]]:
    """Keys of every installed game, or nothing at all when asking failed.

    Empty on failure rather than raising: the grid is still a grid with no
    badges, and an older client that does not know the subcommand answers
    exit 0 with no output, which lands here as "nothing installed".
    """
    try:
        done = subprocess.run(
            [gotg_bin(), "complete", "installed"],
            capture_output=True,
            timeout=INSTALLED_TIMEOUT,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if done.returncode != 0:
        return set()
    found: set[tuple[str, str]] = set()
    for line in done.stdout.splitlines():
        platform, sep, game_id = line.strip().partition("/")
        if sep and platform and game_id:
            found.add((platform, game_id))
    return found
