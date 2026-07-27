"""Command line entry point (IMPORTER_SPEC.md §10).

The Kubernetes CronJob invokes this image with ``--once`` and configures everything
else through the environment, so the flag surface here is a stable interface.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config as cfgmod
from . import rules as rulesmod
from .plan import ACTION_MANUAL, Op
from .planner import plan_source, summarize

log = logging.getLogger("gotg-importer")

EXIT_OK = 0
EXIT_CONFIG = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gotg-importer",
        description="Organize completed game torrents into the GOTG /Games tree.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one pass over the qBittorrent work queue and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and write nothing",
    )
    parser.add_argument(
        "--bootstrap",
        metavar="DIR",
        nargs="+",
        help=(
            "process these source directories by path instead of polling qBittorrent; "
            "accepts several paths, or one comma-separated list"
        ),
    )
    parser.add_argument(
        "--rules",
        metavar="FILE",
        type=Path,
        default=None,
        help="path to rules.yaml (defaults to the copy bundled in the image)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _print_plan(ops: list[Op]) -> None:
    """One line per operation: action platform src -> dst id."""
    for op in ops:
        dst = op.dst or "-"
        line = f"{op.action:<9} {op.platform or '-':<9} {op.src} -> {dst}"
        if op.entry_id:
            line += f"  [{op.entry_id}]"
        if op.reason:
            line += f"  ({op.reason})"
        print(line)


def _bootstrap_paths(raw: list[str]) -> list[Path]:
    """Resolve --bootstrap arguments to directories.

    IMPORTER_SPEC.md §10 documents a comma-separated list, but No-Intro directory
    names are full of commas ("Zelda, The - ... (En,Fr,Es)"), so a path that exists
    as given is always taken whole; only non-existent tokens are split on commas.
    """
    paths: list[Path] = []
    for token in raw:
        candidate = Path(token).expanduser()
        if candidate.exists() or "," not in token:
            paths.append(candidate)
            continue
        paths.extend(Path(part.strip()).expanduser() for part in token.split(",") if part.strip())
    if not paths:
        raise cfgmod.ConfigError("--bootstrap needs at least one directory")
    return paths


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.once and not args.bootstrap:
        log.error("nothing to do: pass --once (queue) or --bootstrap DIR[,DIR...]")
        return EXIT_CONFIG

    try:
        cfg = cfgmod.load(require_qbit=bool(args.once))
        rules = rulesmod.load(args.rules)
    except (cfgmod.ConfigError, rulesmod.RulesError) as exc:
        log.error("%s", exc)
        return EXIT_CONFIG

    ops: list[Op] = []
    if args.bootstrap:
        try:
            sources = _bootstrap_paths(args.bootstrap)
        except cfgmod.ConfigError as exc:
            log.error("%s", exc)
            return EXIT_CONFIG
        for path in sources:
            try:
                ops.extend(plan_source(path, cfg.games_root, rules))
            except FileNotFoundError:
                log.error("source does not exist: %s", path)
                return EXIT_CONFIG
    else:
        log.error("--once needs the qBittorrent work queue, which is not wired up yet")
        return EXIT_CONFIG

    if args.dry_run:
        _print_plan(ops)
    else:
        log.error("execution is not wired up yet; re-run with --dry-run")
        return EXIT_CONFIG

    manual = sum(1 for op in ops if op.action == ACTION_MANUAL)
    log.info("%s", summarize(ops))
    if manual:
        log.info("%d payload(s) need manual review", manual)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
