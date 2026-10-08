"""The picker restarting into a newer build of itself.

`gotg update self` leaves a new picker at the root Steam starts; this process
is still the old one, with the old client on its PATH. Rather than tell the
person to quit and come back, the loop ends with a Restart and `__main__`
execs this pid into the new root's `gotg-ui`.

The pid is the point. danstick's daemon was started `--follow <pid>` and
follows *this* pid; `execvpe` keeps it, and keeps the environment it reads
(`DANSTICK_SKIP_DAEMON_CHECK`, `DANSTICK_FOLLOW`, danstick.py), so the new
picker's `ensure_daemon` returns at once and the seats people took survive
the update. A new danstick shipped with the update runs against the old
daemon until the next session: the seats are worth more than the newer
daemon for the rest of one evening. Pure apart from the exec, so the
command and the client's word can be tested.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NoReturn

from .launch import LaunchError

# The client's last line after `gotg update self`: where the new picker is.
SAID_PICKER = "picker\t"


@dataclass(frozen=True)
class Restart:
    """What run() hands back instead of a game: the root to restart from."""

    root: str


def command(root: str, argv: list[str]) -> list[str]:
    """The new picker, with the arguments this one was started with: a
    `--platform` or `--installed` somebody launched with still holds."""
    return [os.path.join(root, "bin", "gotg-ui"), *argv]


def said_root(lines: Iterable[str]) -> str | None:
    """The root the client named, from the lines it printed -- the last such
    line, as a retry would say it again. None when it said nothing: an older
    client that does not print it."""
    found = [line[len(SAID_PICKER) :].strip() for line in lines if line.startswith(SAID_PICKER)]
    return found[-1] if found and found[-1] else None


def usable(root: str) -> bool:
    """A root worth restarting into: a store path with the picker in it. The
    word comes from the client's output, and the client is already trusted
    -- the picker execs it -- but a line of build noise is not a root."""
    real = os.path.realpath(root)
    return real.startswith("/nix/store/") and os.access(os.path.join(real, "bin", "gotg-ui"), os.X_OK)


def exec_restart(restart: Restart, argv: list[str] | None = None) -> NoReturn:
    """Become the new picker. Raises LaunchError when the exec cannot happen."""
    cmd = command(restart.root, sys.argv[1:] if argv is None else argv)
    try:
        os.execvpe(cmd[0], cmd, os.environ)
    except (OSError, ValueError) as error:
        raise LaunchError(f"could not restart into {cmd[0]}: {error}") from error
    raise LaunchError(f"could not restart into {cmd[0]}")  # pragma: no cover - execvpe does not return
