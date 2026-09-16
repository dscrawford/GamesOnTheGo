"""`config/`, read once, as one dictionary.

Everything in that directory, whatever shape it is in:

    config/theme.yaml            -> config.get("theme")
    config/icons.yaml            -> config.get("icons")
    config/controllers/snes.yaml -> config.get("controllers")["snes"]

A file becomes a key named after it; a directory becomes a key holding one
entry per file in it. Nothing here knows what any of them mean -- the modules
that read a section are the ones that know, and this is only the loading, the
caching and the one place that decides where `config/` is.

It exists because the alternative was in front of us: `BACKGROUND` was defined
in three modules, as (18, 18, 20) in two of them and (18, 18, 22) in the third,
and the same grey was called TEXT in one file and LABEL in another. Values that
are written down once cannot drift like that.

Missing is not an error. A key nobody has written yet has a default at the call
site, so a config directory that is absent entirely leaves the picker looking
exactly as it did when these were constants in Python.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

import yaml


def config_dir() -> pathlib.Path:
    """Where `config/` is.

    GOTG_CONFIG is what the wrapper sets, because the installed picker's copy
    lives in the store beside it. The fallback is the checkout, so running from
    the tree needs nothing set.
    """
    override = os.environ.get("GOTG_CONFIG")
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parents[3] / "config"


def _read(path: pathlib.Path) -> Any:
    """One file, or None when it cannot be read.

    One bad file costs its own section and nothing else. A picker that refused
    to start over a stray tab in a colour table would be a worse failure than
    the one it is reporting.
    """
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def read(directory: pathlib.Path) -> dict[str, Any]:
    """Every file and subdirectory, as one dictionary. Uncached."""
    out: dict[str, Any] = {}
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.is_dir():
            section = {
                path.stem: value
                for path in sorted(entry.glob("*.yaml"))
                if (value := _read(path)) is not None
            }
            if section:
                out[entry.name] = section
        elif entry.suffix in (".yaml", ".yml"):
            value = _read(entry)
            if value is not None:
                out[entry.stem] = value
    return out


_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """The whole of `config/`, read once."""
    global _cache
    if _cache is None:
        _cache = read(config_dir())
    return _cache


def forget() -> None:
    """Drop the cache, so the next read picks up an edit. For tests, and for
    anything that changes GOTG_CONFIG after import."""
    global _cache
    _cache = None


def get(path: str, default: Any = None) -> Any:
    """One value, by dotted path: `get("theme.colours.text")`.

    A missing key is the default rather than an error, because every caller
    has a sensible one and none of them wants a traceback on a television.
    """
    node: Any = load()
    for step in path.split("."):
        if not isinstance(node, dict) or step not in node:
            return default
        node = node[step]
    return node


def colour(path: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """A colour, as pygame wants it.

    YAML gives back a list; pygame is happy with either, but a tuple is what
    the rest of this code passes around and mixing the two makes equality in a
    test depend on which half wrote the value.
    """
    value = get(path)
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        try:
            return tuple(int(part) for part in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    return default
