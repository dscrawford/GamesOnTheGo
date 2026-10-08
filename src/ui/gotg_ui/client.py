"""Asking the client a question, the one way.

One binary lookup (`launch.gotg_bin`), one decoding and one failure mode. Strict
decoding would raise on a bad byte in a game's name, so it replaces.

None means "no answer": the client is missing, hung past the timeout, or
exited non-zero. An empty string is an answer -- an older client says exactly
that for a subcommand it does not know -- and what it means is the caller's.
"""

from __future__ import annotations

import subprocess

from . import trace
from .launch import gotg_bin


def ask(argv: list[str], timeout: float) -> str | None:
    """Run `gotg <argv>` and return its stdout, or None when it gave no answer.

    Never raises. The timeout is the caller's, because what a hung client
    costs differs: a badge on the grid can wait a second, a menu cannot.
    """
    command = [gotg_bin(), *argv]
    try:
        done = subprocess.run(  # noqa: S603 -- argv is ours, shell=False
            command, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        trace.say("client", argv=argv, failed=type(error).__name__)
        return None
    if done.returncode != 0:
        trace.say("client", argv=argv, code=done.returncode)
        return None
    return done.stdout
