"""What the picker does next, decided from values, before it touches anything.

Each answers "given this, what happens" with no effect of its own: it returns a
small frozen description, and `run()` carries it out -- spawning the Preparer,
saying the chip's word, execing. The questions that cost something (is the game
ready, is that root usable) arrive as callables and are asked only on the paths
that need them.

No pygame.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from . import updates
from .chip import ChipState
from .restart import said_root

IGNORE = "ignore"
OPEN_SAVES = "open-saves"
OPEN_STORAGE = "open-storage"
PREPARE = "prepare"
INSTALL = "install"
UPDATE = "update"
CANCEL_INSTALL = "cancel-install"
ADOPT = "adopt"
START = "start"


@dataclass(frozen=True)
class Pick:
    """One thing to do about a menu choice. `args` None is the loader's
    default (prepare the game's environment). `sets_after` says that what the
    loader does on success (`after`, possibly None) is part of this; without
    it the previous value stands."""

    action: str
    args: tuple[str, ...] | None = None
    variant: str | None = None
    version: str | None = None
    after: str | None = None
    sets_after: bool = False


def plan_pick(
    game,
    verb: str,
    variant: str | None,
    version: str | None,
    *,
    installing: bool,
    ready: Callable[[], bool],
) -> Pick:
    """What a verb chosen in a game's menu does. `installing` is whether an
    install of this game is already running; `ready` asks the client whether a
    launch would do work (a subprocess, so asked only for the verbs that
    launch)."""
    if game is None:
        return Pick(IGNORE)
    if verb == "saves":
        return Pick(OPEN_SAVES, variant=variant, version=version)
    if verb == "storage":
        return Pick(OPEN_STORAGE)
    if verb == "steam-add":
        # Not an exec: the shortcut is written, the grid comes back. A mod
        # gets its own Steam entry, which is what the client does with a
        # variant anyway -- its own launcher, its own artwork.
        return Pick(PREPARE, ("steam", "add"), variant, version, after=None, sets_after=True)
    if verb == "install":
        # Behind the grid, as a ring on the tile: the badge fills in when it
        # lands, and the grid stays usable meanwhile. Per variant, like play --
        # a mod is its own environment, and installing it here is what makes
        # its Play instant later.
        return Pick(INSTALL, variant=variant, version=version)
    if verb == "cancel-install":
        return Pick(CANCEL_INSTALL)
    if verb == "update":
        return Pick(UPDATE)
    if verb == "uninstall":
        # Through the loader like steam-add, so what was removed is read
        # rather than guessed; then the badges are asked for again. No
        # variant: `gotg uninstall` takes the game's bytes and every launcher
        # with them, mods included. Passing one would read as "remove just
        # this mod", which is not what would happen.
        return Pick(PREPARE, ("uninstall",), after="uninstall", sets_after=True)
    # A game already on its way: the loader adopts that install rather than
    # starting a second one to queue behind the client's lock.
    if verb == "play" and installing:
        return Pick(ADOPT, after=verb, sets_after=True)
    # Readiness is per variant: a mod is its own environment, and the one
    # built for the plain game says nothing about whether this one is.
    if ready():
        return Pick(START, variant=variant, version=version, after=verb, sets_after=True)
    return Pick(PREPARE, None, variant, version, after=verb, sets_after=True)


NOTHING = "nothing"
SAY = "say"
RESTART = "restart"
UPDATING = "start-update"


@dataclass(frozen=True)
class SelfUpdate:
    action: str
    root: str | None = None
    chip: str | None = None


def plan_self_update(
    report: updates.Report | None,
    running_root: str | None,
    *,
    chip: ChipState,
    now: float,
    busy: bool,
) -> SelfUpdate:
    """The chip pressed: restart into a newer picker already at the root, or
    `gotg update self` through the loader -- unless something else is running
    (`busy`: installs or a loader), since two writers of the library's lock
    would queue and a person would see neither."""
    if chip.updating(now):
        return SelfUpdate(NOTHING)
    # The report's word, not the transient one: "failed" ten seconds ago is no
    # reason to refuse the second press.
    words = updates.chip(report, running_root)
    if words not in (updates.RESTART_TO_UPDATE, updates.UPDATE_AVAILABLE):
        return SelfUpdate(NOTHING)
    if busy:
        return SelfUpdate(SAY, chip="busy")
    if words == updates.RESTART_TO_UPDATE and report is not None and report.picker:
        return SelfUpdate(RESTART, root=report.picker)
    return SelfUpdate(UPDATING, chip="updating")


NOOP = "noop"
REFUSE = "refuse"
GO = "go"


@dataclass(frozen=True)
class Restarting:
    action: str
    root: str
    chip: str | None = None
    refresh: bool = False


def plan_restart(root: str, mine: str | None, usable: Callable[[str], bool]) -> Restarting:
    """The new picker is at `root`: become it, keeping the pid the daemon
    follows. Nothing to restart into when this picker already is that root, or
    when it is the dev shell's checkout (no GOTG_UI_SELF): then Steam's copy is
    current and the chip says which for a while."""
    if mine is None or os.path.normpath(root) == os.path.normpath(mine):
        return Restarting(NOOP, root, "up-to-date" if mine is not None else "updated-here", refresh=True)
    if not usable(root):
        return Restarting(REFUSE, root, "failed")
    return Restarting(GO, root)


@dataclass(frozen=True)
class PrepareDone:
    """What a finished loader leaves to do, in the order `run()` carries it
    out. `begin` is a (game, verb, variant, version) for `start`; `launch` a
    tuple to hand to the caller as the choice."""

    failed: bool = False
    clear: bool = False
    clear_after: bool = False
    clear_restoring: bool = False
    refresh: bool = False
    forget_versions: bool = False
    restart_root: str | None = None
    chip: str | None = None
    begin: tuple | None = None
    launch: tuple | None = None


def on_prepare_done(
    after: str | None,
    ok: bool,
    lines: Iterable[str],
    *,
    picker: str | None,
    restoring: tuple | None,
    starting: tuple | None,
    game,
    variant: str | None,
    version: str | None,
) -> PrepareDone:
    """The loader stopped running. `after` is what it was for, `lines` its
    last output, `picker` the report's picker root; `restoring` and `starting`
    are the game a restore and a saves check were for; `game`, `variant` and
    `version` are the loader's own."""
    if not ok:
        return PrepareDone(failed=True, chip="failed" if after == "self-update" else None)
    if after is None:
        return PrepareDone(clear=True)  # steam add done -- back to the grid
    if after == "self-update":
        # An older client says nothing: the root, as it is.
        root = said_root(lines) or picker
        if root:
            return PrepareDone(clear=True, clear_after=True, restart_root=root)
        return PrepareDone(clear=True, clear_after=True, chip="up-to-date", refresh=True)
    if after in ("install", "uninstall"):
        # What the client said about versions was about a game that is no
        # longer there -- or not there yet.
        return PrepareDone(clear=True, refresh=True, forget_versions=True)
    if after == "restored" and restoring is not None:
        # The save is back: play it, asking about saves as any play does --
        # this machine is now simply ahead.
        g, v, ver = restoring
        return PrepareDone(clear=True, clear_restoring=True, begin=(g, "play", v, ver))
    if after == "kept" and starting is not None:
        # A save kept: the game starts with it, and no second question -- the
        # two sides now agree.
        return PrepareDone(clear=True, launch=starting)
    return PrepareDone(clear=True, begin=(game, after, variant, version))


CHOOSE = "choose"
LAUNCH = "launch"
DROP = "drop"


def plan_saves_answer(found, waiting: bool) -> str:
    """The saves check came back (`found` None when it failed or the client
    could not say): a conflict is a choice between the two, anything else
    starts the game -- a launch is never held on a question. `waiting` is
    whether a launch is still waiting on it; an answer to one that was backed
    out of is dropped."""
    if not waiting:
        return DROP
    return CHOOSE if found is not None and found.conflict else LAUNCH
