"""Executes a plan against the filesystem.

Two invariants hold everywhere in this module:

* **Sources are never modified.** Originals stay seeding, so the only operation
  that touches disk is a hardlink from one.
* **Every write lands under ``GAMES_ROOT``.** Destinations are checked, not
  trusted, so a bad rule or a crafted torrent name cannot escape the tree.

Only hardlinks are materialized. Everything a source has to be *unpacked or
converted* into is the client's to make, from the raw members the catalog
names — a recipe runs once per machine, and the server ships the bytes it
already has rather than a second copy. That keeps a transfer the size of the
release, and the server out of the business of decompressing cartridge dumps.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .manifest import Entry
from .plan import (
    ACTION_ARCHIVE,
    ACTION_ATTACH,
    ACTION_CONVERT,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    ACTION_SKIP,
    Op,
)

log = logging.getLogger("gotg-importer")

STATUS_DONE = "done"
STATUS_NOOP = "noop"
STATUS_MANUAL = "manual"
STATUS_SKIP = "skip"
STATUS_ERROR = "error"

# Prefixes of the staging directories earlier importers left behind while
# they still unpacked on the server.
STAGING_PREFIXES = (".gotg-extract-", ".gotg-zip-")

# Hashing drops its page cache as it goes: the pod's memory limit counts
# cached pages, and a 17 GB archive would fill it.
CACHE_DROP_BYTES = 256 * 1024 * 1024

# The actions whose result is a recipe on the client, not a file here.
CLIENT_ACTIONS = frozenset({ACTION_EXTRACT, ACTION_CONVERT, ACTION_ARCHIVE})


class ExecutionError(Exception):
    """One operation failed. The torrent is tagged gotg-error and the run continues."""


@dataclass(frozen=True)
class Result:
    op: Op
    status: str
    message: str = ""
    entry: Entry | None = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_DONE, STATUS_NOOP, STATUS_SKIP)


def _assert_under(path: Path, root: Path) -> Path:
    """Refuse to write outside the games tree, whatever the rules or names say."""
    resolved = Path(os.path.normpath(path))
    root = Path(os.path.normpath(root))
    if not resolved.is_relative_to(root):
        raise ExecutionError(f"refusing to write outside {root}: {resolved}")
    return resolved


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def cleanup_staging(games_root: Path) -> int:
    """Delete staging directories left by a run that was killed mid-extract.

    Python's TemporaryDirectory cleanup does not run when the process is SIGKILLed
    — an OOM kill or an evicted pod — so without this a failed extract silently
    strands several gigabytes until someone notices.
    """
    removed = 0
    if not games_root.is_dir():
        return 0
    for platform_dir in games_root.iterdir():
        if not platform_dir.is_dir():
            continue
        for child in platform_dir.iterdir():
            if child.is_dir() and child.name.startswith(STAGING_PREFIXES):
                log.warning("removing staging directory left by an earlier run: %s", child)
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
    return removed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    since_drop = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
            since_drop += len(chunk)
            if since_drop >= CACHE_DROP_BYTES:
                since_drop = 0
                try:
                    os.posix_fadvise(fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                except OSError:
                    pass
    return digest.hexdigest()


def write_sidecar(path: Path) -> str:
    """Write ``<file>.sha256`` (bare hex) if absent; return the digest either way."""
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.exists():
        existing = sidecar.read_text().strip().split()[0]
        if existing:
            return existing
    digest = sha256_file(path)
    tmp = sidecar.with_suffix(".sha256.tmp")
    tmp.write_text(digest + "\n")
    os.replace(tmp, sidecar)
    return digest


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


def _link(src: Path, dst: Path) -> str:
    """Hardlink src -> dst, never clobbering. Returns the status."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if _same_file(src, dst):
            return STATUS_NOOP
        raise ExecutionError(f"destination exists and is a different file: {dst}")
    try:
        os.link(src, dst)
    except OSError as exc:
        if exc.errno == 18:  # EXDEV
            raise ExecutionError(f"{src} and {dst} are on different filesystems; hardlinks need one volume") from exc
        raise ExecutionError(f"link {src} -> {dst}: {exc}") from exc
    return STATUS_DONE


def _entry_for(op: Op, cfg: Config, dst: Path, digest: str | None) -> Entry:
    size = dst.stat().st_size if dst.is_file() else _dir_size(dst)
    return Entry(
        platform=op.platform,
        path=cfg.server_path(dst),
        type=op.type,
        size_bytes=size,
        sha256=digest,
        title=op.title,
    )


def execute(op: Op, cfg: Config, *, checksum: bool = True) -> Result:
    """Carry out one planned operation. Never raises; failures come back as Results."""
    if op.action == ACTION_SKIP:
        return Result(op, STATUS_SKIP, op.reason)
    if op.action == ACTION_MANUAL:
        return Result(op, STATUS_MANUAL, op.reason)
    if op.action == ACTION_ATTACH:
        # Nothing to write: the extra is served from where it seeds, on the
        # base game's entry. Publishing is where it exists at all.
        return Result(op, STATUS_DONE, op.reason)
    if op.action in CLIENT_ACTIONS:
        # The raw members are what the catalog names; the client's recipe
        # does the unpacking. Nothing to write, and nothing for the manifest.
        return Result(op, STATUS_DONE, "raw members published; the client's recipe unpacks them")
    if op.action != ACTION_HARDLINK:
        return Result(op, STATUS_ERROR, f"unsupported action {op.action!r}")

    try:
        dst = _assert_under(Path(op.dst), cfg.games_root)
        status = _link(Path(op.src), dst)
        digest = write_sidecar(dst) if checksum else None
        return Result(op, status, entry=_entry_for(op, cfg, dst, digest))
    except ExecutionError as exc:
        return Result(op, STATUS_ERROR, str(exc))
    except OSError as exc:
        return Result(op, STATUS_ERROR, f"{op.action} {op.src}: {exc}")
