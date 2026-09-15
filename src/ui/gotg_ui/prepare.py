"""Getting a game ready before the exec, with the UI as the screen.

The first launch of a platform builds its emulator, and under Steam that build
used to hide behind a zenity dialog — an X11 window gamescope in game mode may
never composite, leaving a black screen indistinguishable from a hang, with
the errors in a log file nobody is reading. The UI is an SDL window gamescope
does show, so it becomes the progress surface: run `gotg install`, stream its
output into a loader view, and only then do the usual quit-and-exec.

Nothing here decides *how* to prepare a game — `gotg install` owns downloads,
builds and recipes, and `gotg complete ready` owns knowing whether any of that
is needed. This module just runs them and holds the lines.
"""

from __future__ import annotations

import collections
import os
import signal
import subprocess
import threading

from .catalog import Game
from .launch import gotg_bin

# Enough scrollback to show why a build failed; nix errors run long.
TAIL_LINES = 200

# The ready check is pure filesystem on the client's side and measures ~120ms;
# a ceiling 25x that is a hung client, and every second of it is a frozen
# frame loop under somebody's thumb.
READY_TIMEOUT = 3


def is_ready(game: Game, variant: str | None = None) -> bool:
    """Whether launching would do work. Filesystem-only on the client's side —
    no nix evaluation, no network — so the grid can afford to ask on every
    pick. The wrapper pins the exact client on PATH, so the two halves of this
    protocol always ship together and the exit code can be trusted plainly.

    Any failure to ask is a no: erring toward "prepare" costs one idempotent
    install run with a visible screen; erring toward "ready" execs into a
    build with no screen, which is the failure this module exists to end.
    """
    try:
        done = subprocess.run(
            [gotg_bin(), "complete", "ready", f"{game.platform}/{game.id}", *([variant] if variant else [])],
            capture_output=True,
            timeout=READY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # The word as well as the code: an older client's `complete` answers any
    # unknown subcommand with exit 0 and no output, which would read as ready
    # and exec into a build with no screen.
    return done.returncode == 0 and done.stdout.strip() == b"ready"


class Preparer:
    """One `gotg install`, streaming.

    The child gets GOTG_NO_DIALOG so the client raises no zenity beside the
    loader view that is already showing its output. Lines land in a bounded
    deque from a reader thread; the frame loop polls `running` and `tail()`
    and never blocks.
    """

    def __init__(self, game: Game, argv: list[str] | None = None, variant: str | None = None):
        self.game = game
        self.variant = variant
        # Default is the install; the menu also runs `steam add` through the
        # same loader, since both are long, narrated, and cancellable.
        self.argv = argv or ["install"]
        self._lines: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self._lock = threading.Lock()
        env = dict(os.environ)
        env["GOTG_NO_DIALOG"] = "1"
        try:
            # stderr folded into stdout: the client narrates on stderr
            # (log/warn), nix reports on stderr, and the loader wants one
            # stream in order.
            self.process = subprocess.Popen(  # noqa: S603 — argv is ours, shell=False
                [gotg_bin(), *self.argv, f"{game.platform}/{game.id}", *([variant] if variant else [])],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                errors="replace",
                # Its own process group: a SIGTERM to bash alone leaves nix
                # and curl running, and an orphaned curl racing the retry's
                # resume corrupts the shared .part file.
                start_new_session=True,
            )
        except OSError as error:
            # A missing client must land on the failure screen like any other
            # failed prepare — is_ready survives this exact case, and raising
            # out of pick() would take the event loop down instead.
            self.process = None
            self._lines.append(f"could not start {gotg_bin()}: {error}")
            return
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._lock:
                self._lines.append(line.rstrip("\n"))
        self.process.stdout.close()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def ok(self) -> bool:
        """Only meaningful once not running."""
        return self.process is not None and self.process.returncode == 0

    def tail(self, count: int = 12) -> list[str]:
        with self._lock:
            lines = list(self._lines)
        return lines[-count:]

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def cancel(self) -> None:
        """B on the loader. TERM to the whole group, so nix and curl stop with
        the bash between them — then the client's own traps and .part files
        do their usual job for the next attempt."""
        if not self.running:
            return
        self._signal_group(signal.SIGTERM)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Unkillable in D-state on a dead mount; the frame loop must
                # not die for it.
                pass
