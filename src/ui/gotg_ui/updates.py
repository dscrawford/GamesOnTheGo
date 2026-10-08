"""What is out of date, asked of the client, as the grid draws it.

The client owns the answer (`gotg complete updates`, src/client/lib/updates.sh):
whether the library is behind where it came from, whether the roots Steam
starts are what the library would build, and which installed games have a
root that is not what the lock would build or a release the install lacks.
This module only reads that answer and turns it into a badge and a chip --
the decisions a frame needs, with no pygame in them, so they are tested.

An older client prints nothing for a subcommand it does not know, and a
version it does not speak is the same as nothing: no chip, no badges.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .launch import gotg_bin

# The answer is a cache and a walk of the disk; a hung client must not hold
# the window closed.
ASK_TIMEOUT = 3
VERSION = 1

# What a tile wears, in order of what matters most: an install on its way,
# an update waiting, here, or nothing.
RING = "ring"
ALERT = "alert"
INSTALLED = "installed"
NONE = ""


@dataclass(frozen=True)
class GameUpdate:
    key: tuple[str, str]
    attrs: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    available: bool
    behind: bool
    unbuilt: bool
    writable: bool
    stale: bool
    pending: bool
    picker: str | None
    games: Mapping[tuple[str, str], GameUpdate]


def parse(text: str) -> Report | None:
    """The client's JSON as a Report; None for nothing, garbage, or a version
    this picker does not speak -- all read as "nothing to say"."""
    try:
        data = json.loads(text or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    gotg = data.get("gotg") if isinstance(data.get("gotg"), dict) else {}
    games: dict[tuple[str, str], GameUpdate] = {}
    for item in data.get("games") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not isinstance(key, str):
            continue
        platform, sep, game_id = key.partition("/")
        if not (sep and platform and game_id):
            continue
        attrs = tuple(a for a in (item.get("attrs") or []) if isinstance(a, str))
        reasons = tuple(r for r in (item.get("reasons") or []) if isinstance(r, str))
        if reasons:
            games[(platform, game_id)] = GameUpdate((platform, game_id), attrs, reasons)
    picker = gotg.get("picker")
    return Report(
        available=bool(gotg.get("available")),
        behind=bool(gotg.get("behind")),
        unbuilt=bool(gotg.get("unbuilt")),
        writable=bool(data.get("writable")),
        stale=bool(data.get("stale")),
        pending=bool(data.get("pending")),
        picker=picker if isinstance(picker, str) and picker else None,
        games=games,
    )


def outdated(report: Report | None, installed: Iterable[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    """The installed games with an update waiting. Installed is the client's
    word too; a game the report names that is not here any more is nothing."""
    if report is None:
        return frozenset()
    here = set(installed)
    return frozenset(key for key in report.games if key in here)


def badge(
    key: tuple[str, str],
    installed: Iterable[tuple[str, str]],
    waiting: Iterable[tuple[str, str]],
    rings: Iterable[tuple[str, str]],
) -> str:
    """What one tile wears: the ring of an install on its way wins, then the
    exclamation of an update waiting, then the arrow of a game that is here."""
    if key in set(rings):
        return RING
    if key in set(waiting):
        return ALERT
    if key in set(installed):
        return INSTALLED
    return NONE


# What the chip at the top right says, if anything.
UPDATE_AVAILABLE = "Update available"
RESTART_TO_UPDATE = "Restart to update"
UPDATING = "Updating…"
UPDATE_FAILED = "Update failed"
UPDATED = "Updated — this picker runs the checkout"
AFTER_INSTALLS = "Update after installs finish"


def chip(report: Report | None, self_root: str | None, phase: str | None = None) -> str | None:
    """The words at the top right, or None for none.

    `self_root` is the store path this picker runs from (GOTG_UI_SELF, set by
    the packaged wrapper and not by the dev shell). A root Steam starts that
    already holds a newer picker than this one needs only a restart. A
    library that cannot be written -- a store copy, from `nix run <lib>#ui`
    with nothing configured -- can be behind all it likes: no chip, since
    nothing here could move its pin. `phase` is the loop's word while an
    update runs or has just ended, and wins.
    """
    if phase == "updating":
        return UPDATING
    if phase == "failed":
        return UPDATE_FAILED
    if phase == "updated":
        return UPDATED
    if phase == "busy":
        return AFTER_INSTALLS
    if report is None or not report.writable:
        return None
    if self_root and report.picker and report.picker != self_root:
        return RESTART_TO_UPDATE
    if report.available:
        return UPDATE_AVAILABLE
    return None


def self_root() -> str | None:
    """The root this picker runs from, when it is a packaged one."""
    return os.environ.get("GOTG_UI_SELF") or None


class Check:
    """`gotg update --check`, running: the network and the evaluation behind
    the cached answer, in its own process group at a lower priority, so the
    loop can kill the lot on its way out -- an evaluation left running after
    the picker became the game would take the game's CPU on a Deck.

    Started only after `ask()` has answered: a client too old to know
    `--check` would read `update` with arguments as the full rebuild.
    """

    def __init__(self) -> None:
        self.process: subprocess.Popen | None
        try:
            self.process = subprocess.Popen(  # noqa: S603 — argv is ours, shell=False
                [gotg_bin(), "update", "--check"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                preexec_fn=lambda: os.nice(10),  # noqa: PLW1509 — a single-threaded child
            )
        except OSError:
            self.process = None

    def done(self) -> bool:
        return self.process is None or self.process.poll() is not None

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except OSError:
            pass


def ask() -> Report | None:
    """`gotg complete updates`: the cached answer, in a moment. None when the
    client is missing, hung, failed, or too old to know the question."""
    try:
        done = subprocess.run(  # noqa: S603 — argv is ours, shell=False
            [gotg_bin(), "complete", "updates"],
            capture_output=True,
            timeout=ASK_TIMEOUT,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return parse(done.stdout)
