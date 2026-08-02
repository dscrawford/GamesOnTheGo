"""Composes scan -> classify -> plan into one call per source payload.

This is the whole read-only half of the importer: given a path, say exactly what
would be written to /Games and why, without touching anything.
"""

from __future__ import annotations

import collections
from pathlib import Path

from . import classify as cl
from . import plan as pl
from .rules import Rules
from .scan import Source, scan


def _rom_files(source: Source, rules: Rules) -> list[str]:
    """Payload files that look like ROMs — the input to the No-Intro planner."""
    return [name for name in source.files if cl.is_rom_name(name, rules)]


def _scene_inner_name(source: Source, rules: Rules) -> str:
    """The ROM inside a scene release.

    Prefer a copy already unpacked beside the volumes, then the archive's own
    listing. Both name the real ROM; the .rar name would only give a volume.
    """
    for candidates in (source.files, source.archive_members):
        roms = [name for name in candidates if cl.is_rom_name(name, rules)]
        if roms:
            return max(roms, key=len)
    rars = source.with_ext("rar")
    return rars[0] if rars else source.name


def plan_source(path: Path | str, games_root: Path | str, rules: Rules) -> list[pl.Op]:
    """Plan every operation for one completed torrent payload."""
    source = scan(Path(path))
    verdict = cl.classify(source, rules)
    games_root = str(games_root).rstrip("/")
    src_dir = str(source.path.parent)

    if verdict.handler == cl.HANDLER_EXCLUDED:
        return [pl.Op(pl.ACTION_SKIP, verdict.platform, str(source.path), "", "", reason=verdict.reason or "excluded")]

    if verdict.handler == cl.HANDLER_MANUAL:
        return [pl.Op(pl.ACTION_MANUAL, verdict.platform, str(source.path), "", "", reason=verdict.reason)]

    if not verdict.platform:
        # Without a platform there is no valid destination, and guessing one files
        # the game where the client would hand it to the wrong emulator.
        return [
            pl.Op(
                pl.ACTION_MANUAL,
                "",
                str(source.path),
                "",
                "",
                reason=verdict.reason or f"no platform for handler {verdict.handler!r}",
            )
        ]

    if verdict.handler == cl.HANDLER_WIIU_DECRYPTED:
        return [pl.plan_wiiu_decrypted(games_root, src_dir, source.name)]

    if verdict.handler == cl.HANDLER_WIIU_NUS:
        return [pl.plan_wiiu_nus(games_root, src_dir, source.name)]

    if verdict.handler == cl.HANDLER_SCENE_ARCHIVE:
        region = rules.region_for_release(source.name)
        return [
            pl.plan_scene_archive(
                verdict.platform, games_root, src_dir, source.name, _scene_inner_name(source, rules), region
            )
        ]

    if verdict.handler == cl.HANDLER_SINGLE_ARCHIVE:
        # The game inside is what matters; the archive is only how it travelled.
        # The target format comes from the rules, so a platform whose emulator
        # wants something else gets a conversion rather than a bare unpack.
        inner = next(
            (m for m in source.archive_members if cl.is_rom_name(m, rules)),
            "",
        )
        return [
            pl.plan_single_archive(
                verdict.platform,
                games_root,
                str(source.path),
                inner,
                target_ext=rules.target_for(verdict.platform).ext,
            )
        ]

    if verdict.handler == cl.HANDLER_NO_INTRO_SET:
        return pl.plan_no_intro_set(
            verdict.platform, games_root, str(source.path), _rom_files(source, rules), one_g_one_r=True
        )

    if verdict.handler == cl.HANDLER_SINGLE_FILE:
        # A single deliberately-downloaded game: keep it even if it is a demo or
        # prototype, so no 1G1R curation here.
        if source.is_dir:
            return pl.plan_no_intro_set(
                verdict.platform, games_root, str(source.path), _rom_files(source, rules), one_g_one_r=False
            )
        return pl.plan_no_intro_set(verdict.platform, games_root, src_dir, [source.name], one_g_one_r=False)

    return [
        pl.Op(
            pl.ACTION_MANUAL, verdict.platform, str(source.path), "", "", reason=f"unknown handler {verdict.handler!r}"
        )
    ]


def summarize(ops: list[pl.Op]) -> str:
    """The one-line run summary the CronJob logs (IMPORTER_SPEC.md §10)."""
    counts: collections.Counter[str] = collections.Counter(op.action for op in ops)
    return " ".join(
        f"{action}={counts.get(action, 0)}"
        for action in (pl.ACTION_HARDLINK, pl.ACTION_EXTRACT, pl.ACTION_ARCHIVE, pl.ACTION_MANUAL, pl.ACTION_SKIP)
    )
