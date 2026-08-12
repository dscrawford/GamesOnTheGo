"""Source payload -> (platform, handler).

Pure: takes the ``Source`` probe from ``scan`` and returns a verdict. Anything the
ladder cannot identify confidently becomes ``manual`` with a reason — the importer
never guesses a platform, because a wrong guess files a game under an id the client
will then hand to the wrong emulator.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass

from ..contract import (  # noqa: F401 — re-exported; the wire-legal six live in the contract
    HANDLER_NO_INTRO_SET,
    HANDLER_SCENE_ARCHIVE,
    HANDLER_SINGLE_ARCHIVE,
    HANDLER_SINGLE_FILE,
    HANDLER_WIIU_DECRYPTED,
    HANDLER_WIIU_NUS,
)
from .rules import Rules
from .scan import VOLUME_RE, WIIU_DECRYPTED_DIRS, Source

# Internal verdicts only — these never become catalog rows.
HANDLER_EXCLUDED = "excluded"
HANDLER_MANUAL = "manual"

_HEX8_RE = re.compile(r"^[0-9a-f]{8}(\.(app|h3))?$", re.I)
_TMD_RE = re.compile(r"^(title\.tmd|tmd(\.[0-9a-f]+)?)$", re.I)


@dataclass(frozen=True)
class Classification:
    handler: str
    platform: str = ""
    reason: str = ""


def _ext(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def _is_wiiu_decrypted(source: Source) -> bool:
    """Decrypted output present: code/ content/ meta/ with an executable in code/."""
    if not set(WIIU_DECRYPTED_DIRS).issubset(source.dirs):
        return False
    return any(f.lower().endswith(".rpx") for f in source.code_files)


def _is_wiiu_nus(source: Source) -> bool:
    """Raw NUS download: a TMD, hash files, and 8-hex-named content."""
    has_tmd = any(_TMD_RE.match(f) for f in source.files)
    has_h3 = source.has_ext("h3")
    has_content = any(_HEX8_RE.match(f) for f in source.files)
    return has_tmd and has_h3 and has_content


def _is_scene_archive(source: Source) -> bool:
    return source.has_ext("rar") and (source.has_ext("r00") or source.has_ext("sfv"))


def is_rom_name(name: str, rules: Rules) -> bool:
    """Whether a filename could be a ROM rather than packaging.

    Archive metadata and multi-volume parts never count: a scene release is a .rar
    plus dozens of .r00-style volumes, and only the file inside identifies the game.
    """
    ext = _ext(name)
    return bool(ext) and ext not in rules.archive_exts and not VOLUME_RE.match(ext)


def _platform_from_names(names: tuple[str, ...], rules: Rules) -> tuple[str, int]:
    """Majority platform across candidate ROM names, plus how many voted for it."""
    votes: collections.Counter[str] = collections.Counter()
    for name in names:
        if not is_rom_name(name, rules):
            continue
        platform = rules.platform_for_ext(_ext(name))
        if platform:
            votes[platform] += 1
    if not votes:
        return "", 0
    platform, count = votes.most_common(1)[0]
    return platform, count


def _platform_from_files(source: Source, rules: Rules) -> tuple[str, int]:
    return _platform_from_names(source.files, rules)


def classify(source: Source, rules: Rules) -> Classification:
    """Decide how to import one payload. Priority order per IMPORTER_SPEC.md §4."""
    if not source.is_dir:
        platform = rules.platform_for_ext(_ext(source.name))
        if platform:
            return Classification(HANDLER_SINGLE_FILE, platform)

        # A lone archive: the wrapper says nothing, so judge it by what is inside.
        if source.archive_members:
            platform, _ = _platform_from_names(source.archive_members, rules)
            if platform:
                return Classification(HANDLER_SINGLE_ARCHIVE, platform)
            return Classification(
                HANDLER_MANUAL,
                reason="archive whose contents name no known platform; add the extension to rules.yaml",
            )

        return Classification(
            HANDLER_MANUAL,
            reason=f"unmapped extension {_ext(source.name) or '(none)'}; add it to rules.yaml",
        )

    mapped = rules.dat_dirs.get(source.name)
    if mapped:
        platform, handler = mapped
        reason = "excluded by rules.yaml" if handler == HANDLER_EXCLUDED else ""
        return Classification(handler, platform, reason)

    if _is_wiiu_decrypted(source):
        return Classification(HANDLER_WIIU_DECRYPTED, "wiiu", "decrypted code/content/meta present")

    if _is_wiiu_nus(source):
        return Classification(HANDLER_WIIU_NUS, "wiiu", "raw NUS download; decrypt deferred")

    if _is_scene_archive(source):
        # Prefer a ROM already unpacked beside the volumes; otherwise read the
        # names out of the archive header.
        platform, _ = _platform_from_files(source, rules)
        if not platform:
            platform, _ = _platform_from_names(source.archive_members, rules)
        if platform:
            return Classification(HANDLER_SCENE_ARCHIVE, platform)
        return Classification(
            HANDLER_MANUAL,
            reason="scene archive whose contents name no known platform; add the extension to rules.yaml",
        )

    platform, count = _platform_from_files(source, rules)
    if platform and count >= rules.min_set_files:
        return Classification(HANDLER_NO_INTRO_SET, platform)
    if platform and count == 1:
        return Classification(HANDLER_SINGLE_FILE, platform)
    if platform:
        return Classification(HANDLER_NO_INTRO_SET, platform)

    zips = len(source.with_ext("zip"))
    if zips >= rules.min_set_files:
        # A DAT set of zipped ROMs: the archives hide the extension, so only the
        # directory name can identify the platform.
        return Classification(
            HANDLER_MANUAL,
            reason=f"{zips} zipped ROMs but directory name is unmapped; add it to rules.yaml dat_dirs",
        )
    return Classification(HANDLER_MANUAL, reason="no recognizable game payload")
