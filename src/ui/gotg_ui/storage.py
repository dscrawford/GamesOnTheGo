"""Where games are kept, as a screen: the client's directories, with room.

The client owns the list (`gotg configure storage`); this asks it, shows each
directory with the free space on its device, and hands every change back to
the client rather than editing the config itself.
"""

from __future__ import annotations

import json
import subprocess

from .launch import gotg_bin

# A df per directory; a card that has gone to sleep can take a moment.
STORAGE_TIMEOUT = 10


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


class Storage:
    """The directories and the cursor, refreshed from the client on demand."""

    def __init__(self, rows: list[dict] | None = None):
        self.rows: list[dict] = rows or []
        self.selected = 0
        self.message = ""

    @property
    def row(self) -> dict | None:
        return self.rows[self.selected] if 0 <= self.selected < len(self.rows) else None

    def move(self, delta: int) -> None:
        self.selected = max(0, min(len(self.rows) - 1, self.selected + delta))

    def refresh(self) -> None:
        self.rows = list_dirs()
        self.selected = max(0, min(len(self.rows) - 1, self.selected))

    # Every change goes through the client and comes back as its own words.
    def make_default(self) -> None:
        if self.row is not None:
            self.message = _configure("default", self.row["path"])
            self.refresh()

    def remove(self) -> None:
        if self.row is not None:
            self.message = _configure("remove", self.row["path"])
            self.refresh()

    def add(self, path: str) -> None:
        if path.strip():
            self.message = _configure("add", path.strip())
            self.refresh()


def list_dirs() -> list[dict]:
    """The directories as the client reports them; empty when asking failed."""
    try:
        done = subprocess.run(
            [gotg_bin(), "configure", "storage", "list", "--json"],
            capture_output=True,
            timeout=STORAGE_TIMEOUT,
            text=True,
            errors="replace",
        )
        rows = json.loads(done.stdout) if done.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("path"), str)]


def _configure(verb: str, path: str) -> str:
    """Run one storage change; the client's last line is the message shown."""
    try:
        done = subprocess.run(
            [gotg_bin(), "configure", "storage", verb, path],
            capture_output=True,
            timeout=STORAGE_TIMEOUT,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"could not run the client: {error}"
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    if done.returncode != 0:
        return lines[-1] if lines else f"gotg configure storage {verb} failed"
    return lines[0] if lines else ""
