"""`gotg saves sync --all` behind the grid, on a cadence. The sync decides
what is safe to move; this only decides when."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass

from .launch import gotg_bin

# A minute after the picker is up, so the first frames are not fighting a
# dozen bundles for the disk.
FIRST_SECONDS = 60.0
EVERY_SECONDS = 600.0
LAUNCH_WAITS_FOR_SYNC_SECONDS = 10.0


@dataclass(frozen=True)
class Cadence:
    next_at: float

    @classmethod
    def start(cls, now: float) -> Cadence:
        return cls(next_at=now + FIRST_SECONDS)

    def due(self, now: float) -> bool:
        return now >= self.next_at

    def ran(self, now: float) -> Cadence:
        return Cadence(next_at=now + EVERY_SECONDS)


class Sync:
    """In its own process group at a lower priority, as updates.Check runs:
    killed on the way out rather than left to take a game's CPU."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None
        nice = shutil.which("nice")
        prefix = [nice, "-n", "10"] if nice else []
        try:
            self.process = subprocess.Popen(  # noqa: S603 — argv is ours, shell=False
                [*prefix, gotg_bin(), "saves", "sync", "--all"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            self.process = None

    def done(self) -> bool:
        return self.process is None or self.process.poll() is not None

    def stop(self, grace: float = 0.0) -> None:
        """A game about to start must not open saves a pull is halfway through
        placing, so a handoff waits up to `grace`; a quit does not."""
        if self.done():
            return
        try:
            self.process.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except OSError:
            pass


def enabled() -> bool:
    return os.environ.get("GOTG_UI_SAVES_SYNC", "1") != "0"
