"""Asking the client a question, the one way.

Eight modules (installed, updates, prepare's readiness, versions, variants,
the saves list and check, storage's listing) each ran `gotg ...` with their own
copy of the same try/except, the same text decoding and the same "non-zero
means no answer". They had begun to differ by accident -- four decoded strictly
and a bad byte in a game's name would have been an uncaught UnicodeDecodeError
in the one that did. Here there is one binary lookup (`launch.gotg_bin`), one
decoding (replace, never raise), and one failure mode: None.

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
