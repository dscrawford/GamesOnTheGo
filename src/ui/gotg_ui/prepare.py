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
import time
from dataclasses import dataclass

from . import config
from .catalog import Game
from .launch import gotg_bin

# Enough scrollback to show why a build failed; nix errors run long.
TAIL_LINES = 200

# The ready check is pure filesystem on the client's side and measures ~120ms;
# a ceiling 25x that is a hung client, and every second of it is a frozen
# frame loop under somebody's thumb.
READY_TIMEOUT = int(config.get("theme.timeouts.ready", 3))


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


# What the client says while a download runs, one line per tick on stderr:
#
#     progress <bytes so far> <bytes expected> <bytes per second> <what>
#
# tab-separated, asked for with GOTG_PROGRESS_LINES=1. Kept as the latest
# figure rather than as text, because a screen full of numbers is not a bar.
PROGRESS_PREFIX = "progress\t"


@dataclass(frozen=True)
class Progress:
    done: int
    total: int  # 0 when the server did not say
    rate: int  # bytes per second, averaged since the transfer began
    what: str

    @property
    def fraction(self) -> float | None:
        if self.total <= 0:
            return None
        return min(1.0, self.done / self.total)

    def describe(self) -> str:
        """The figures, in words for a television: how far, how fast, how long."""
        parts = []
        if self.total > 0:
            parts.append(f"{int(self.done * 100 // self.total)}% · {_human(self.done)} of {_human(self.total)}")
        else:
            parts.append(_human(self.done))
        if self.rate > 0:
            parts.append(f"{_human(self.rate)}/s")
            if self.total > self.done:
                parts.append(f"{elapsed_text((self.total - self.done) // self.rate)} left")
        return " · ".join(parts)


def parse_progress(line: str) -> Progress | None:
    """One of the client's progress lines, or None for anything else."""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    fields = line.split("\t")
    if len(fields) < 5:
        return None
    try:
        return Progress(done=int(fields[1]), total=int(fields[2]), rate=int(fields[3]), what="\t".join(fields[4:]))
    except ValueError:
        return None


# What nix is doing while an environment builds, one line per change, from
# the client's filter over nix's own progress (src/client/lib/env.sh):
#
#     stage <eval|fetch|build> <done> <total> <what>
#
# Evaluation has no end nix can name, so it counts files read and sweeps.
STAGE_PREFIX = "stage\t"
STAGE_KINDS = ("eval", "fetch", "build")


@dataclass(frozen=True)
class Stage:
    kind: str
    done: int
    total: int  # 0 for evaluation, which has no end nix can name
    what: str

    @property
    def fraction(self) -> float | None:
        if self.total <= 0:
            return None
        return min(1.0, self.done / self.total)

    def describe(self) -> str:
        if self.kind == "eval":
            return f"evaluating {self.what or 'the emulator'} · {self.done} files read"
        verb = "fetching" if self.kind == "fetch" else "building"
        return f"{verb} the emulator · {self.done} of {self.total}"


def parse_stage(line: str) -> Stage | None:
    """One of the client's stage lines, or None for anything else."""
    if not line.startswith(STAGE_PREFIX):
        return None
    fields = line.split("\t")
    if len(fields) < 5 or fields[1] not in STAGE_KINDS:
        return None
    try:
        return Stage(kind=fields[1], done=int(fields[2]), total=int(fields[3]), what="\t".join(fields[4:]))
    except ValueError:
        return None


def _human(size: int) -> str:
    """1.5 GB, 300.0 MB, 12 B: the same scale the client prints."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def elapsed_text(seconds: float) -> str:
    """5s, 1m 05s, 1h 01m. A build says this so a long one is visibly alive."""
    whole = int(seconds)
    if whole >= 3600:
        return f"{whole // 3600}h {(whole % 3600) // 60:02d}m"
    if whole >= 60:
        return f"{whole // 60}m {whole % 60:02d}s"
    return f"{whole}s"


class Preparer:
    """One `gotg install`, streaming.

    The child gets GOTG_NO_DIALOG so the client raises no zenity beside the
    loader view that is already showing its output, and GOTG_PROGRESS_LINES so
    a download reports where it is. Lines land in a bounded deque from a
    reader thread; the frame loop polls `running`, `tail()` and `progress`
    and never blocks.
    """

    def __init__(
        self,
        game: Game,
        argv: list[str] | None = None,
        variant: str | None = None,
        version: str | None = None,
    ):
        self.game = game
        self.variant = variant
        self.version = version
        # Default is the install; the menu also runs `steam add` through the
        # same loader, since both are long, narrated, and cancellable.
        self.argv = argv or ["install"]
        self._lines: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self._progress: Progress | None = None
        self._stage: Stage | None = None
        self._lock = threading.Lock()
        self.started = time.monotonic()
        env = dict(os.environ)
        env["GOTG_NO_DIALOG"] = "1"
        env["GOTG_PROGRESS_LINES"] = "1"
        try:
            # stderr folded into stdout: the client narrates on stderr
            # (log/warn), nix reports on stderr, and the loader wants one
            # stream in order.
            self.process = subprocess.Popen(  # noqa: S603 — argv is ours, shell=False
                [
                    gotg_bin(),
                    *self.argv,
                    f"{game.platform}/{game.id}",
                    *([variant] if variant else []),
                    *(["--version", version] if version else []),
                ],
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
            text = line.rstrip("\n")
            progress = parse_progress(text)
            stage = parse_stage(text) if progress is None else None
            with self._lock:
                if progress is not None:
                    self._progress = progress
                elif stage is not None:
                    self._stage = stage
                else:
                    self._lines.append(text)
        self.process.stdout.close()

    @property
    def progress(self) -> Progress | None:
        """Where the current download is, or None outside one."""
        with self._lock:
            return self._progress

    @property
    def stage(self) -> Stage | None:
        """What nix is doing for the environment, or None before it says."""
        with self._lock:
            return self._stage

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def running(self) -> bool:
        # And until the reader has drained the pipe: exit comes before the last lines.
        if self.process is None:
            return False
        return self.process.poll() is None or self._reader.is_alive()

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
