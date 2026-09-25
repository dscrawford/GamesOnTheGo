"""What the picker decided about controllers, written down as it happens.

`GOTG_UI_TRACE=/path/to/file` and every pad SDL opens, every press it
delivers and whether it was taken, every danstick event and command, and every
keyboard node held quiet lands there as one JSON object per line. Off unless
asked for: the picker is a 60 Hz loop and a file write per frame is not free.

For the conversation where the picker did something on somebody's machine
that it does not do on the machine the tests run on. A person can press the
buttons; this is what lets somebody else see what the picker saw.
"""

from __future__ import annotations

import json
import os
import time

_path = os.environ.get("GOTG_UI_TRACE") or ""
_started = time.monotonic()


def on() -> bool:
    return bool(_path)


def say(kind: str, **fields) -> None:
    """One line, or nothing at all when tracing is off."""
    if not _path:
        return
    # The pid, because the picker and the gate append to one file -- the
    # picker execs into the launch, so their lines interleave and only this
    # says which program decided what.
    line = {"t": round(time.monotonic() - _started, 3), "pid": os.getpid(), "kind": kind, **fields}
    try:
        with open(_path, "a") as out:
            out.write(json.dumps(line, default=str) + "\n")
    except OSError:
        pass


# When each pad's hold was last heard from, for `progress_gap`.
_heard: dict[str, float] = {}

# Longer than this between two readings of one hold is worth a line. danstick
# ticks every 20 ms; the screen used to take 50 ms of quiet as a release.
GAP = 0.04


def progress_gap(event: dict, now: float | None = None) -> None:
    """One line when a hold's readings pause, and nothing otherwise.

    Readings are not traced -- fifty a second per pad would drown the rest --
    but the pauses between them are what made a steady press flash back to
    the red empty seat: danstick sends progress from the loop that also rescans
    every input device, and on a machine with many of them a rescan can
    outlast the screen's patience. How long it really goes quiet is a
    property of this machine, not of any test's, so it is measured here.
    """
    if not on() or event.get("event") != "progress":
        return
    key = str(event.get("node") or event.get("name") or "")
    now = time.monotonic() if now is None else now
    frac = event.get("frac")
    if not isinstance(frac, (int, float)) or frac <= 0:
        _heard.pop(key, None)
        return
    last = _heard.get(key)
    if last is not None and now - last > GAP:
        say("progress-gap", node=key, ms=round((now - last) * 1000), frac=round(float(frac), 3))
    _heard[key] = now
