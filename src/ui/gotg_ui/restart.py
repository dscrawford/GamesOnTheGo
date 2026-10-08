"""The picker restarting into a newer build of itself.

`gotg update self` leaves a new picker at the root Steam starts; this process
is still the old one, with the old client on its PATH. Rather than tell the
person to quit and come back, the loop ends with a Restart and `__main__`
execs this pid into the new root's `gotg-ui`.

The pid is the point. danstick's daemon was started `--follow <pid>` and
follows *this* pid; `execvp` keeps it, and keeps the environment it reads
(`DANSTICK_SKIP_DAEMON_CHECK`, `DANSTICK_FOLLOW`, danstick.py), so the new
picker's `ensure_daemon` returns at once and the seats people took survive
the update. A new danstick shipped with the update runs against the old
daemon until the next session: the seats are worth more than the newer
daemon for the rest of one evening. Pure apart from the exec, so the
command and the environment can be tested.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .launch import LaunchError


@dataclass(frozen=True)
class Restart:
    """What run() hands back instead of a game: the root to restart from."""

    root: str


def command(root: str, argv: list[str]) -> list[str]:
    """The new picker, with the arguments this one was started with: a
    `--platform` or `--installed` somebody launched with still holds."""
    return [os.path.join(root, "bin", "gotg-ui"), *argv]


def environment(env: Mapping[str, str]) -> dict[str, str]:
    """The environment the new picker starts with: this one's, untouched.
    What the daemon handoff rests on is in it already; nothing is added, and
    a copy rather than the mapping itself so the caller's is not mutated."""
    return dict(env)


def exec_restart(restart: Restart, argv: list[str] | None = None) -> None:
    """Become the new picker. Raises LaunchError when the exec cannot happen
    -- the root has no gotg-ui, or it cannot be run -- which is reachable:
    execvpe raises before it replaces anything."""
    import sys

    cmd = command(restart.root, sys.argv[1:] if argv is None else argv)
    try:
        os.execvpe(cmd[0], cmd, environment(os.environ))
    except OSError as error:
        raise LaunchError(f"could not restart into {cmd[0]}: {error}") from error
