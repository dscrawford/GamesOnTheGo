"""Executes a plan against the filesystem.

Two invariants hold everywhere in this module:

* **Sources are never modified.** Originals stay seeding, so the only operations
  are hardlinks from them and derived files written elsewhere.
* **Every write lands under ``GAMES_ROOT``.** Destinations are checked, not
  trusted, so a bad rule or a crafted torrent name cannot escape the tree.

Every operation is idempotent: an already-imported entry is a no-op, never a
clobber, so re-running the CronJob costs one stat per game.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .manifest import Entry
from .plan import (
    ACTION_ARCHIVE,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    ACTION_SKIP,
    Op,
)
from .scan import WIIU_DECRYPTED_DIRS

log = logging.getLogger("gotg-importer")

STATUS_DONE = "done"
STATUS_NOOP = "noop"
STATUS_MANUAL = "manual"
STATUS_SKIP = "skip"
STATUS_ERROR = "error"

# Headroom required beyond the estimated size of a derived file.
FREE_SPACE_MARGIN = 1.10

# Prefixes of the temporary directories used while building a derived file.
STAGING_PREFIXES = (".gotg-extract-", ".gotg-zip-")


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


def _require_space(root: Path, needed: int) -> None:
    free = shutil.disk_usage(root).free
    if free < needed * FREE_SPACE_MARGIN:
        raise ExecutionError(f"need ~{needed / 1e9:.1f} GB under {root} but only {free / 1e9:.1f} GB free")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
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


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    log.debug("run: %s (cwd=%s)", " ".join(cmd), cwd)
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise ExecutionError(f"{cmd[0]} failed ({proc.returncode}): {' / '.join(tail)}")


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


def _find_extracted(src_dir: Path, ext: str) -> Path | None:
    """An already-unpacked ROM sitting next to the archive volumes.

    Scene uploads often ship the extracted file alongside the RAR set, and a
    previous run may have left one. Hardlinking it costs nothing and skips an
    expensive unpack of several gigabytes.
    """
    matches = sorted(src_dir.glob(f"*.{ext}"), key=lambda p: p.stat().st_size, reverse=True)
    return matches[0] if matches else None


def _verify_archive(src_dir: Path) -> None:
    sfvs = sorted(src_dir.glob("*.sfv"))
    if sfvs and shutil.which("rhash"):
        _run(["rhash", "-c", sfvs[0].name], cwd=src_dir)


def _extract(op: Op, cfg: Config) -> tuple[str, Path]:
    src_dir = Path(op.src)
    dst = _assert_under(Path(op.dst), cfg.games_root)
    ext = dst.suffix.lstrip(".")

    if dst.exists():
        return STATUS_NOOP, dst

    already = _find_extracted(src_dir, ext)
    if already is not None:
        log.info("using ROM already unpacked in the source: %s", already.name)
        return _link(already, dst), dst

    rars = sorted(src_dir.glob("*.rar"))
    if not rars:
        raise ExecutionError(f"no .rar volume to extract in {src_dir}")

    _verify_archive(src_dir)
    _require_space(cfg.games_root, _dir_size(src_dir))
    dst.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=dst.parent, prefix=".gotg-extract-") as tmp:
        _run(["unrar", "x", "-o-", "-idq", str(rars[0]), tmp + "/"])
        produced = [p for p in Path(tmp).rglob(f"*.{ext}") if p.is_file()]
        if not produced:
            raise ExecutionError(f"archive in {src_dir} produced no .{ext} file")
        winner = max(produced, key=lambda p: p.stat().st_size)
        os.replace(winner, dst)  # same filesystem: atomic
    return STATUS_DONE, dst


def _archive(op: Op, cfg: Config) -> tuple[str, Path]:
    """Pack a decrypted WiiU title's code/content/meta into one zip."""
    src_dir = Path(op.src)
    dst = _assert_under(Path(op.dst), cfg.games_root)

    if dst.exists():
        return STATUS_NOOP, dst

    members = [d for d in WIIU_DECRYPTED_DIRS if (src_dir / d).is_dir()]
    if not members:
        raise ExecutionError(f"nothing to archive in {src_dir}")

    _require_space(cfg.games_root, sum(_dir_size(src_dir / m) for m in members))
    dst.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=dst.parent, prefix=".gotg-zip-") as tmp:
        staged = Path(tmp) / dst.name
        # -1 keeps a multi-GB pack fast; this content barely compresses anyway.
        _run(["zip", "-r", "-1", "-q", str(staged), *members], cwd=src_dir)
        os.replace(staged, dst)
    return STATUS_DONE, dst


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

    try:
        if op.action == ACTION_HARDLINK:
            dst = _assert_under(Path(op.dst), cfg.games_root)
            status = _link(Path(op.src), dst)
        elif op.action == ACTION_EXTRACT:
            status, dst = _extract(op, cfg)
        elif op.action == ACTION_ARCHIVE:
            status, dst = _archive(op, cfg)
        else:
            return Result(op, STATUS_ERROR, f"unsupported action {op.action!r}")

        digest = write_sidecar(dst) if (checksum and dst.is_file()) else None
        return Result(op, status, entry=_entry_for(op, cfg, dst, digest))
    except ExecutionError as exc:
        return Result(op, STATUS_ERROR, str(exc))
    except OSError as exc:
        return Result(op, STATUS_ERROR, f"{op.action} {op.src}: {exc}")
