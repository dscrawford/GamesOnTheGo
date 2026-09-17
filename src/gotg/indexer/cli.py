"""Command line entry point (IMPORTER_SPEC.md §10).

The Kubernetes CronJob invokes this image with ``--scan`` and configures
everything else through the environment, so the flag surface is a stable
interface. The qBittorrent queue mode is gone: production has scanned the
download tree without WebUI credentials since the first deploy.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from . import config as cfgmod
from . import publish as pub
from . import rules as rulesmod
from .run import run_paths, run_scan

log = logging.getLogger("gotg-importer")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gotg-importer",
        description="Organize completed game torrents into the GOTG /Games tree.",
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
        "--scan",
        metavar="DIR",
        type=Path,
        default=None,
        help=(
            "walk one directory, import everything that classifies as a game, and ignore the rest; needs no qBittorrent"
        ),
    )
    parser.add_argument(
        "--match",
        metavar="REGEX",
        default=None,
        help=(
            "with --scan: import only the sources whose name the pattern is found in (case-insensitive), "
            "and skip the sweep -- seconds for one release rather than minutes for the library"
        ),
    )
    parser.add_argument(
        "--rules",
        metavar="FILE",
        type=Path,
        default=None,
        help="path to rules.yaml (defaults to the copy bundled in the image)",
    )
    parser.add_argument(
        "--no-checksum",
        action="store_true",
        help="skip sha256 sidecars (faster first import, no client-side verification)",
    )
    parser.add_argument(
        "--diff-catalog",
        action="store_true",
        help="compare the manifest against the service catalog and exit; the dual-publish gate",
    )
    parser.add_argument(
        "--allow-unhashed",
        action="store_true",
        help="publish catalog rows without a sha256 (bytes the client cannot verify)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


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

    if not args.bootstrap and not args.scan and not args.diff_catalog:
        log.error("nothing to do: pass --scan DIR, --bootstrap DIR [DIR...], or --diff-catalog")
        return EXIT_CONFIG

    try:
        cfg = cfgmod.load()
        rules = rulesmod.load(args.rules)
        paths = _bootstrap_paths(args.bootstrap) if args.bootstrap else []
        for path in paths:
            if not path.exists():
                raise cfgmod.ConfigError(f"source does not exist: {path}")
        if args.scan and not args.scan.is_dir():
            raise cfgmod.ConfigError(f"not a directory: {args.scan}")
        if args.match is not None:
            if not args.scan:
                raise cfgmod.ConfigError("--match narrows a --scan; with --bootstrap the paths are already the choice")
            try:
                re.compile(args.match)
            except re.error as exc:
                raise cfgmod.ConfigError(f"--match is not a valid pattern: {exc}") from exc
    except (cfgmod.ConfigError, rulesmod.RulesError) as exc:
        log.error("%s", exc)
        return EXIT_CONFIG

    if args.diff_catalog:
        if not cfg.api_url:
            log.error("--diff-catalog needs GOTG_API_URL and GOTG_INDEX_TOKEN")
            return EXIT_CONFIG
        from . import manifest as mf

        entries = mf.load(cfg.manifest_path)
        try:
            problems = pub.diff_catalog(entries, pub.CatalogAPI(cfg.api_url, cfg.index_token))
        except pub.PublishError as exc:
            log.error("%s", exc)
            return EXIT_FAILED
        for problem in problems:
            log.warning("%s", problem)
        log.info("diff: %d problem(s) across %d manifest entr(ies)", len(problems), len(entries))
        return EXIT_OK if not problems else EXIT_FAILED

    checksum = not args.no_checksum

    # Dual-publish is best-effort by design: the /Games tree is still the
    # thing clients run on, so a down API degrades to the old world with a
    # loud line rather than failing the import.
    publisher = None
    if cfg.api_url and not args.dry_run:
        try:
            publisher = pub.Publisher(
                pub.CatalogAPI(cfg.api_url, cfg.index_token),
                allow_unhashed=args.allow_unhashed,
            )
        except pub.PublishError as exc:
            log.error("catalog publishing disabled for this run: %s", exc)

    try:
        if args.scan:
            stats = run_scan(
                args.scan,
                cfg,
                rules,
                dry_run=args.dry_run,
                checksum=checksum,
                publisher=publisher,
                match=args.match,
            )
        elif paths:
            stats = run_paths(paths, cfg, rules, dry_run=args.dry_run, checksum=checksum, publisher=publisher)
    except OSError as exc:
        log.error("%s", exc)
        return EXIT_FAILED

    log.info("%s", stats.summary())
    # Per-torrent failures are reported and tagged, not fatal: a full run that
    # imported 200 games and flagged 2 is a success the CronJob should not retry.
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
