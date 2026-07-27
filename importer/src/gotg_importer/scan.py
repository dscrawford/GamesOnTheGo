"""The filesystem probe — the only I/O in the classify/plan path.

Everything downstream operates on a ``Source`` value, so classification and planning
stay pure and can be tested against synthetic trees with no fixtures on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Decrypted-WiiU marker dirs; probed one level deeper to confirm an executable.
WIIU_DECRYPTED_DIRS = ("code", "content", "meta")


@dataclass(frozen=True)
class Source:
    """One import candidate: a completed torrent's payload path."""

    path: Path
    is_dir: bool
    files: tuple[str, ...] = ()  # immediate child file names
    dirs: tuple[str, ...] = ()  # immediate child directory names
    code_files: tuple[str, ...] = ()  # names inside code/, when that dir exists

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


def scan(path: Path) -> Source:
    """Probe one payload path. Raises FileNotFoundError if it does not exist."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    if not path.is_dir():
        return Source(path=path, is_dir=False)

    files: list[str] = []
    dirs: list[str] = []
    for child in sorted(path.iterdir()):
        (dirs if child.is_dir() else files).append(child.name)

    code_files: tuple[str, ...] = ()
    if "code" in dirs:
        code_dir = path / "code"
        code_files = tuple(sorted(c.name for c in code_dir.iterdir() if c.is_file()))

    return Source(
        path=path,
        is_dir=True,
        files=tuple(files),
        dirs=tuple(dirs),
        code_files=code_files,
    )
