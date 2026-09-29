"""Two saves for one game, and choosing which to keep before it starts.

Every machine keeps its own saves and the GOTG service keeps the last few
generations. Two machines that both played since they last agreed have two
different saves, and the client refuses to merge them -- it used to settle a
launch by warning and playing whatever was on this machine, which on a sofa
is a warning nobody reads and a save that quietly goes nowhere.

So before a game starts the picker asks the client (`gotg saves check
--json`) and, when the answer is a conflict, puts both saves on screen: the
machine each one is on and when it was last updated. The one chosen is kept
with `gotg saves keep ... here|remote`, which never destroys the other -- a
pull archives what was here first, and a forced push leaves the service's
generation for retention. Anything short of a conflict (the same save, one side
newer, no service) starts the game as it always did; the client pulls a
newer remote on the way in by itself.

The model is pure: it reads the client's JSON and knows which card is lit.
The checking runs on a worker (`check`) because it bundles and hashes the
saves, which is not a frame's worth of work.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime

from .catalog import Game
from .launch import gotg_bin

# Bundling a big save tree and asking the service. A launch that waits longer
# than this starts the game rather than waiting on a network.
CHECK_TIMEOUT = 30

HERE = "here"
REMOTE = "remote"


@dataclass(frozen=True)
class Side:
    """One of the two saves: the machine it is on and when it last changed."""

    device: str
    updated: str  # ISO time, or "" when nothing says


@dataclass(frozen=True)
class Check:
    state: str
    here: Side
    remote: Side | None

    @property
    def conflict(self) -> bool:
        return self.state == "conflict"


def parse(output: str) -> Check | None:
    """The client's `saves check --json`, or None for anything else -- a
    launch is never held up by an answer it cannot read."""
    for line in reversed(output.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("here"), dict):
            continue
        here = data["here"]
        remote = data.get("remote") if isinstance(data.get("remote"), dict) else None
        return Check(
            state=str(data.get("state") or ""),
            here=Side(str(here.get("device") or ""), str(here.get("updated") or "")),
            remote=Side(str(remote.get("device") or ""), str(remote.get("updated") or "")) if remote else None,
        )
    return None


def check(game: Game, variant: str | None) -> Check | None:
    """Ask the client. None when it could not answer in time or at all."""
    argv = [gotg_bin(), "saves", "check", f"{game.platform}/{game.id}", *([variant] if variant else []), "--json"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=CHECK_TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    return parse(done.stdout) if done.returncode == 0 else None


def when(updated: str, now: datetime | None = None) -> str:
    """An ISO time as a person reads it, in this machine's time zone:
    "today, 21:10", "yesterday, 08:02", "Sep 20, 10:00", "Sep 20 2025"."""
    if not updated:
        return "unknown"
    try:
        moment = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return updated
    now = (now or datetime.now()).astimezone()
    days = (now.date() - moment.date()).days
    clock = moment.strftime("%H:%M")
    if days == 0:
        return f"today, {clock}"
    if days == 1:
        return f"yesterday, {clock}"
    if moment.year == now.year:
        return f"{moment.strftime('%b')} {moment.day}, {clock}"
    return f"{moment.strftime('%b')} {moment.day} {moment.year}"


@dataclass(frozen=True)
class Choice:
    """The screen: the game about to start, both saves, and which card is lit.

    The newer save is lit first, since it is the likelier keep; left and right
    move between the two, A keeps the lit one, B goes back to the grid.
    """

    game: Game
    variant: str | None
    version: str | None
    check: Check
    selected: str = HERE

    @classmethod
    def open(cls, game: Game, variant: str | None, version: str | None, found: Check) -> Choice:
        remote = found.remote.updated if found.remote else ""
        first = REMOTE if remote > found.here.updated else HERE
        return cls(game, variant, version, found, first)

    def move(self, dx: int) -> Choice:
        if dx < 0:
            return replace(self, selected=HERE)
        if dx > 0:
            return replace(self, selected=REMOTE)
        return self

    def pick(self, side: str) -> Choice:
        return replace(self, selected=side if side in (HERE, REMOTE) else self.selected)
