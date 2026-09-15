"""One import pass: pick up work, plan it, carry it out, record what happened.

Failure is always per-torrent. One unreadable payload or full disk marks that
torrent and the run continues, because a library import that stops at the first
bad apple is worse than one that reports it and moves on.
"""

from __future__ import annotations

import collections
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import classify as cl
from . import manifest as mf
from . import plan as pl
from . import publish as pub
from . import scan as sc
from .config import Config
from .execute import CLIENT_ACTIONS, STATUS_ERROR, STATUS_MANUAL, Result, cleanup_staging, execute
from .planner import plan_source
from .rules import Rules

log = logging.getLogger("gotg-importer")

# The classifier's own words for "this is a ROM set I cannot place".
UNMAPPED_SET_RE = re.compile(r"zipped ROMs but directory name is unmapped")


@dataclass
class RunStats:
    actions: collections.Counter[str] = field(default_factory=collections.Counter)
    statuses: collections.Counter[str] = field(default_factory=collections.Counter)
    publish_errors: int = 0

    def record(self, results: list[Result]) -> None:
        for r in results:
            self.actions[r.op.action] += 1
            self.statuses[r.status] += 1

    @property
    def failed(self) -> bool:
        return bool(self.statuses[STATUS_ERROR])

    def summary(self) -> str:
        """The one-line run summary (IMPORTER_SPEC.md §10)."""
        parts = [
            f"{action}={self.actions[action]}"
            for action in (
                pl.ACTION_HARDLINK,
                pl.ACTION_EXTRACT,
                pl.ACTION_ARCHIVE,
                pl.ACTION_ATTACH,
                pl.ACTION_MANUAL,
                pl.ACTION_SKIP,
            )
        ]
        parts.append(f"unchanged={self.statuses['noop']}")
        parts.append(f"error={self.statuses[STATUS_ERROR]}")
        if self.publish_errors:
            parts.append(f"unpublished={self.publish_errors}")
        return " ".join(parts)


def print_plan(ops: list[pl.Op]) -> None:
    """action platform src -> dst [id] (reason)"""
    for op in ops:
        line = f"{op.action:<9} {op.platform or '-':<9} {op.src} -> {op.dst or '-'}"
        if op.entry_id:
            line += f"  [{op.entry_id}]"
        if op.reason:
            line += f"  ({op.reason})"
        print(line)


def _log_result(result: Result) -> None:
    if result.status == STATUS_ERROR:
        log.error("%s %s: %s", result.op.action, result.op.src, result.message)
    elif result.status == STATUS_MANUAL:
        log.warning("manual review: %s (%s)", result.op.src, result.message)
    elif result.op.action in CLIENT_ACTIONS:
        log.info("%s %s (raw members; the client unpacks)", result.status, result.op.entry_id or result.op.src)
    else:
        log.info("%s %s -> %s", result.status, result.op.entry_id or result.op.src, result.op.dst or "-")


def process_source(
    path: Path,
    cfg: Config,
    rules: Rules,
    entries: dict[str, mf.Entry],
    *,
    checksum: bool = True,
    ops: list[pl.Op] | None = None,
) -> list[Result]:
    """Plan and execute one payload, folding its entries into the catalog."""
    if ops is None:
        ops = plan_source(path, cfg.games_root, rules)
    results = []
    for op in ops:
        result = execute(op, cfg, checksum=checksum)
        _log_result(result)
        if result.entry is not None:
            entries[result.entry.path] = result.entry
        results.append(result)
    return results


def discover(root: Path, rules: Rules) -> tuple[list[tuple[Path, sc.Source]], int]:
    """Game payloads directly under ``root``, each with its probe, and how many
    entries were ignored.

    The probe comes back with the path because planning needs the same one: a
    scan is a directory walk and an `unrar l` per scene release, and doing it
    again to plan what discovery already looked at is half of this phase.

    The source tree is shared with film and television, so anything that names no
    known game format is passed over silently. That is the whole difference from
    --bootstrap: an unrecognised entry there is a low-confidence game worth a
    person's attention, whereas here it is almost certainly a TV episode, and
    quarantining thousands of those would bury the few that matter.

    Silently, with one exception. A directory of zipped ROMs is unmistakably a
    game set — the archives hide the extension, so only the directory name can
    name the platform — and one whose name is unmapped is a whole console
    missing from the library. That is not a TV episode and must never be
    counted quietly: 1926 Game Boy games sat behind this number until somebody
    went looking.
    """
    found: list[tuple[Path, sc.Source]] = []
    ignored = 0
    unmapped: list[tuple[str, str]] = []
    for child in sorted(root.iterdir()):
        try:
            source = sc.scan(child)
        except OSError as exc:
            log.debug("skipping %s: %s", child.name, exc)
            ignored += 1
            continue
        verdict = cl.classify(source, rules)
        if verdict.handler == cl.HANDLER_MANUAL and not verdict.platform:
            if UNMAPPED_SET_RE.search(verdict.reason):
                unmapped.append((child.name, verdict.reason))
            ignored += 1
            continue
        found.append((child, source))

    for name, reason in unmapped:
        log.warning("a game set is going unimported: %s — %s", name, reason)
    return found, ignored


def run_scan(
    root: Path,
    cfg: Config,
    rules: Rules,
    *,
    dry_run: bool,
    checksum: bool = True,
    publisher: pub.Publisher | None = None,
) -> RunStats:
    """Import every game under one directory (--scan)."""
    since = pub.utc_now()
    started = time.monotonic()
    scanned, ignored = discover(root, rules)
    log.info(
        "scanned %s in %.1fs: %d game source(s), %d entry(s) ignored",
        root,
        time.monotonic() - started,
        len(scanned),
        ignored,
    )
    stats = run_paths(
        [path for path, _ in scanned],
        cfg,
        rules,
        dry_run=dry_run,
        checksum=checksum,
        publisher=publisher,
        sources={path: source for path, source in scanned},
    )

    # The games tree is the raw source for everything whose torrent no longer
    # seeds — most of the library. Published after the torrent pass, so a
    # living source always wins.
    if publisher and not dry_run:
        published, errors = pub.publish_games_root(publisher, mf.load(cfg.manifest_path), cfg)
        log.info("games-root pass: %d published, %d error(s)", published, errors)
        stats.publish_errors += errors

    # Everything this run found unchanged, said once. The sweep below reads
    # seen_at to decide what has vanished, and an entry nobody rewrote has an
    # old one — so without this, skipping identical writes would report the
    # library as gone.
    if publisher and not dry_run:
        try:
            seen = publisher.touch()
            log.info("unchanged: %d entry(s) marked as seen", seen)
        except pub.PublishError as exc:
            log.error("marking unchanged entries as seen: %s", exc)
            stats.publish_errors += 1

    # Only a completed full enumeration may sweep: a partial or failed pass
    # would report the whole untouched library as vanished.
    if publisher and not dry_run and not stats.failed and not stats.publish_errors:
        try:
            publisher.sweep(since)
        except pub.PublishError as exc:
            log.error("sweep: %s", exc)
            stats.publish_errors += 1
    return stats


def _plan_all(
    paths: list[Path],
    cfg: Config,
    rules: Rules,
    sources: dict[Path, sc.Source] | None = None,
) -> list[tuple[Path, list[pl.Op]]]:
    """Every source planned, updates and DLC sorted after the games they attach to.

    An update publishes onto its base's entry, which must exist first; the
    directory order is the release groups' naming. Stable, so the rest keeps
    its given order.
    """
    sources = sources or {}
    planned = [(path, plan_source(path, cfg.games_root, rules, source=sources.get(path))) for path in paths]
    return sorted(planned, key=lambda item: any(op.action == pl.ACTION_ATTACH for op in item[1]))


def run_paths(
    paths: list[Path],
    cfg: Config,
    rules: Rules,
    *,
    dry_run: bool,
    checksum: bool = True,
    publisher: pub.Publisher | None = None,
    sources: dict[Path, sc.Source] | None = None,
) -> RunStats:
    """Import explicit source directories (--bootstrap).

    `sources` are probes the caller already took — a full scan has them, and
    --bootstrap does not.
    """
    stats = RunStats()
    planned = _plan_all(paths, cfg, rules, sources)
    if dry_run:
        ops = [op for _, source_ops in planned for op in source_ops]
        print_plan(ops)
        stats.actions.update(op.action for op in ops)
        return stats

    cleanup_staging(cfg.games_root)
    entries = mf.load(cfg.manifest_path)
    for path, ops in planned:
        results = process_source(path, cfg, rules, entries, checksum=checksum, ops=ops)
        stats.record(results)
        # Publish after every source rather than at the end. A run killed part
        # way through — an OOM during a large extract, an evicted pod — would
        # otherwise leave thousands of imported games with no catalog naming them.
        mf.save(cfg.manifest_path, entries)
        # The catalog rows ride beside the manifest (dual-publish) until the
        # cutover; a publish failure is per-entry and never stops the import.
        if publisher:
            for result in results:
                try:
                    publisher.publish(result, checksum=checksum)
                except pub.PublishError as exc:
                    log.error("publish %s: %s", result.op.entry_id or result.op.src, exc)
                    stats.publish_errors += 1
    return stats
