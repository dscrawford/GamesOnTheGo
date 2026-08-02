"""The filesystem probe — the only I/O in the classify/plan path.

Everything downstream operates on a ``Source`` value, so classification and planning
stay pure and can be tested against synthetic trees with no fixtures on disk.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("gotg-importer")

# Decrypted-WiiU marker dirs; probed one level deeper to confirm an executable.
WIIU_DECRYPTED_DIRS = ("code", "content", "meta")

# Multi-volume archive parts: .r00-.r99, .001-.999, .part2.rar. These are
# packaging, never a ROM, and must not be mistaken for one.
VOLUME_RE = re.compile(r"^(r\d{2,3}|\d{3}|part\d+)$", re.I)

ARCHIVE_LIST_TIMEOUT = 60

# Archives that are packaging around a game rather than a container an emulator
# reads. `.zip` is deliberately absent: the library treats a zipped ROM as a
# first-class entry — ares opens one directly, and hardlinking it costs nothing —
# so unpacking one would trade zero space for a full copy and gain nothing.
SINGLE_ARCHIVE_EXTS = ("7z", "rar")


@dataclass(frozen=True)
class Source:
    """One import candidate: a completed torrent's payload path."""

    path: Path
    is_dir: bool
    files: tuple[str, ...] = ()  # immediate child file names
    dirs: tuple[str, ...] = ()  # immediate child directory names
    code_files: tuple[str, ...] = ()  # names inside code/, when that dir exists
    archive_members: tuple[str, ...] = ()  # names inside a .rar set, without unpacking

    name: str = field(default="")

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(self, "name", self.path.name)

    def has_ext(self, ext: str) -> bool:
        dotted = f".{ext.lower()}"
        return any(f.lower().endswith(dotted) for f in self.files)

    def with_ext(self, ext: str) -> tuple[str, ...]:
        dotted = f".{ext.lower()}"
        return tuple(f for f in self.files if f.lower().endswith(dotted))


def list_archive(path: Path) -> tuple[str, ...]:
    """Names inside a RAR set, read from the header without unpacking.

    A scene release identifies its platform only by the extension of the ROM
    inside the archive, so classification needs this listing whenever the upload
    does not also ship the file already unpacked.
    """
    rars = sorted(path.glob("*.rar"))
    if not rars or not shutil.which("unrar"):
        return ()
    try:
        proc = subprocess.run(
            ["unrar", "lb", str(rars[0])],
            capture_output=True,
            text=True,
            timeout=ARCHIVE_LIST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not list %s: %s", rars[0], exc)
        return ()
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def list_archive_file(path: Path) -> tuple[str, ...]:
    """Names inside a single archive, read from its header without unpacking.

    The archive's own extension says nothing about the platform — a .7z holds
    whatever someone put in it — so the only evidence is what is inside.
    """
    seven = shutil.which("7z") or shutil.which("7za")
    if not seven:
        return ()
    try:
        proc = subprocess.run(
            [seven, "l", "-ba", "-slt", str(path)],
            capture_output=True,
            text=True,
            timeout=ARCHIVE_LIST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not list %s: %s", path, exc)
        return ()
    if proc.returncode != 0:
        return ()
    return tuple(line.partition("=")[2].strip() for line in proc.stdout.splitlines() if line.startswith("Path ="))


def _ext(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def scan(path: Path, *, probe_archives: bool = True) -> Source:
    """Probe one payload path. Raises FileNotFoundError if it does not exist."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    if not path.is_dir():
        members: tuple[str, ...] = ()
        if probe_archives and _ext(path.name) in SINGLE_ARCHIVE_EXTS:
            members = list_archive_file(path)
        return Source(path=path, is_dir=False, archive_members=members)

    files: list[str] = []
    dirs: list[str] = []
    for child in sorted(path.iterdir()):
        (dirs if child.is_dir() else files).append(child.name)

    code_files: tuple[str, ...] = ()
    if "code" in dirs:
        code_dir = path / "code"
        code_files = tuple(sorted(c.name for c in code_dir.iterdir() if c.is_file()))

    members: tuple[str, ...] = ()
    if probe_archives and any(f.lower().endswith(".rar") for f in files):
        members = list_archive(path)

    return Source(
        path=path,
        is_dir=True,
        files=tuple(files),
        dirs=tuple(dirs),
        code_files=code_files,
        archive_members=members,
    )
