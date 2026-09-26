"""The overlay's bar, over the picker as over a game.

A game has `gotg-killswitch` beside it, and its bar is where a controller
joining is drawn and where a room with no controller is told so. The picker
is moving its controller screens into that bar, so the same program runs
beside the picker -- `--overlay-only`: the bar alone, no chord that stops
anything and none that rebinds, since the picker has no console to rebind for.

It follows this process by pid, and this process execvp's into the game with
that pid, so it is stopped before the hand-off: the client starts the game's
own, with the game's platform, and two bars would draw every join twice.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess

from . import trace

# How long it gets to take its bar down before it is killed.
STOP_SECONDS = 1.0


def binary() -> str | None:
    """The kill switch, from GOTG_KILLSWITCH_BIN or PATH; None when neither."""
    return os.environ.get("GOTG_KILLSWITCH_BIN") or shutil.which("gotg-killswitch")


def start(pid: int) -> subprocess.Popen | None:
    """The bar beside `pid`, or None where there is no kill switch or no bar.

    `GOTG_KILLSWITCH_OVERLAY=0` turns the bar off for games, and so here too.
    """
    if os.environ.get("GOTG_KILLSWITCH_OVERLAY", "1") == "0":
        return None
    found = binary()
    if not found:
        trace.say("overlay-absent")
        return None
    try:
        process = subprocess.Popen(
            [found, "--pid", str(pid), "--overlay-only", "--quiet"],
            stdin=subprocess.DEVNULL,
        )
    except OSError as error:
        trace.say("overlay-failed", why=str(error))
        return None
    trace.say("overlay-started", pid=process.pid)
    return process


def stop(process: subprocess.Popen | None) -> None:
    """Take the bar down and wait for it; killed if it will not go."""
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=STOP_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
