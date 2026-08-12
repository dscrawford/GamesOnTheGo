"""One import pass: pick up work, plan it, carry it out, record what happened.

Failure is always per-torrent. One unreadable payload or full disk marks that
torrent and the run continues, because a library import that stops at the first
bad apple is worse than one that reports it and moves on.
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import classify as cl
from . import manifest as mf
from . import plan as pl
from . import publish as pub
from . import scan as sc
from .config import Config
from .execute import STATUS_ERROR, STATUS_MANUAL, Result, cleanup_staging, execute
from .planner import plan_source
from .rules import Rules

log = logging.getLogger("gotg-importer")


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
            for action in (pl.ACTION_HARDLINK, pl.ACTION_EXTRACT, pl.ACTION_ARCHIVE, pl.ACTION_MANUAL, pl.ACTION_SKIP)
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
    else:
        log.info("%s %s -> %s", result.status, result.op.entry_id or result.op.src, result.op.dst or "-")


def process_source(
    path: Path,
    cfg: Config,
    rules: Rules,
    entries: dict[str, mf.Entry],
    *,
    checksum: bool = True,
) -> list[Result]:
    """Plan and execute one payload, folding its entries into the catalog."""
    ops = plan_source(path, cfg.games_root, rules)
    results = []
    for op in ops:
        result = execute(op, cfg, checksum=checksum)
        _log_result(result)
        if result.entry is not None:
            entries[result.entry.path] = result.entry
        results.append(result)
    return results


def discover(root: Path, rules: Rules) -> tuple[list[Path], int]:
    """Game payloads directly under ``root``, and how many entries were ignored.

    The source tree is shared with film and television, so anything that names no
    known game format is passed over silently. That is the whole difference from
    --bootstrap: an unrecognised entry there is a low-confidence game worth a
    person's attention, whereas here it is almost certainly a TV episode, and
    quarantining thousands of those would bury the few that matter.
    """
    found: list[Path] = []
    ignored = 0
    for child in sorted(root.iterdir()):
        try:
            source = sc.scan(child)
        except OSError as exc:
            log.debug("skipping %s: %s", child.name, exc)
            ignored += 1
            continue
        verdict = cl.classify(source, rules)
        if verdict.handler == cl.HANDLER_MANUAL and not verdict.platform:
            ignored += 1
            continue
        found.append(child)
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
    paths, ignored = discover(root, rules)
    log.info("scanned %s: %d game source(s), %d entry(s) ignored", root, len(paths), ignored)
    stats = run_paths(paths, cfg, rules, dry_run=dry_run, checksum=checksum, publisher=publisher)

    # The games tree is the raw source for everything whose torrent no longer
    # seeds — most of the library. Published after the torrent pass, so a
    # living source always wins.
    if publisher and not dry_run:
        published, errors = pub.publish_games_root(publisher, mf.load(cfg.manifest_path), cfg)
        log.info("games-root pass: %d published, %d error(s)", published, errors)
        stats.publish_errors += errors

    # Only a completed full enumeration may sweep: a partial or failed pass
    # would report the whole untouched library as vanished.
    if publisher and not dry_run and not stats.failed and not stats.publish_errors:
        try:
            publisher.sweep(since)
        except pub.PublishError as exc:
            log.error("sweep: %s", exc)
            stats.publish_errors += 1
    return stats


def run_paths(
    paths: list[Path],
    cfg: Config,
    rules: Rules,
    *,
    dry_run: bool,
    checksum: bool = True,
    publisher: pub.Publisher | None = None,
) -> RunStats:
    """Import explicit source directories (--bootstrap)."""
    stats = RunStats()
    if dry_run:
        ops: list[pl.Op] = []
        for path in paths:
            ops.extend(plan_source(path, cfg.games_root, rules))
        print_plan(ops)
        stats.actions.update(op.action for op in ops)
        return stats

    cleanup_staging(cfg.games_root)
    entries = mf.load(cfg.manifest_path)
    for path in paths:
        results = process_source(path, cfg, rules, entries, checksum=checksum)
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
