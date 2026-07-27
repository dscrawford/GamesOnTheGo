"""Classification rules, overridable without a code change.

Defaults are the maps in ``plan.py`` (the validated reference implementation). A
``rules.yaml`` — mounted as a ConfigMap in-cluster — extends or overrides them, so
adding a platform is a config edit rather than a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .plan import DAT_DIR_PLATFORM, EXT_PLATFORM

DEFAULT_RULES_FILE = Path(__file__).with_name("rules.yaml")

# Scene/packaging cruft — never evidence of a platform when guessing from extensions.
ARCHIVE_EXTS = frozenset({"rar", "sfv", "nfo", "par2", "txt", "diz", "md5", "sha1", "jpg", "png"})


class RulesError(Exception):
    """rules.yaml is malformed — fail fast rather than silently misfiling a library."""


@dataclass(frozen=True)
class Rules:
    dat_dirs: dict[str, tuple[str, str]] = field(default_factory=dict)
    extensions: dict[str, str] = field(default_factory=dict)
    archive_exts: frozenset[str] = ARCHIVE_EXTS
    # How many mapped ROM files make a directory a "set" rather than a loose game.
    min_set_files: int = 5
    # Scene releases carry no region tag; this is what they default to.
    scene_default_region: str = "world"
    # (substring, region) pairs that pin a region for known release names.
    scene_overrides: tuple[tuple[str, str], ...] = ()

    def platform_for_ext(self, ext: str) -> str:
        return self.extensions.get(ext.lower().lstrip("."), "")

    def region_for_release(self, release_name: str) -> str:
        """Region for a scene release, whose filename carries no region tag."""
        haystack = release_name.lower()
        for needle, region in self.scene_overrides:
            if needle.lower() in haystack:
                return region
        return self.scene_default_region


def defaults() -> Rules:
    return Rules(dat_dirs=dict(DAT_DIR_PLATFORM), extensions=dict(EXT_PLATFORM))


def load(path: Path | str | None = None) -> Rules:
    """Load rules.yaml, falling back to the built-in defaults when absent."""
    base = defaults()
    path = Path(path) if path is not None else DEFAULT_RULES_FILE
    if not path.exists():
        return base

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise RulesError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RulesError(f"{path}: expected a mapping at the top level")

    dat_dirs = dict(base.dat_dirs)
    for name, spec in (raw.get("dat_dirs") or {}).items():
        if not isinstance(spec, dict) or "platform" not in spec:
            raise RulesError(f"{path}: dat_dirs[{name!r}] needs a 'platform' key")
        dat_dirs[name] = (str(spec["platform"]), str(spec.get("handler", "no_intro_set")))

    extensions = dict(base.extensions)
    for ext, platform in (raw.get("extensions") or {}).items():
        extensions[str(ext).lower().lstrip(".")] = str(platform)

    overrides: list[tuple[str, str]] = []
    for entry in raw.get("scene_overrides") or []:
        if not isinstance(entry, dict) or "match" not in entry or "region" not in entry:
            raise RulesError(f"{path}: each scene_overrides entry needs 'match' and 'region'")
        overrides.append((str(entry["match"]), str(entry["region"])))

    return Rules(
        dat_dirs=dat_dirs,
        extensions=extensions,
        archive_exts=frozenset(raw.get("archive_exts") or base.archive_exts),
        min_set_files=int(raw.get("min_set_files", base.min_set_files)),
        scene_default_region=str(raw.get("scene_default_region", base.scene_default_region)),
        scene_overrides=tuple(overrides),
    )
