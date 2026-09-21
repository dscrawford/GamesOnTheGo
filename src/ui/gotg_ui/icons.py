"""Which drawing stands for the controller somebody is holding.

Two directories, because there are two jobs. `assets/controllers/` is one
diagram per *console*, carrying an anchor circle on every button so a binding
can be labelled; `assets/icons/` is one drawing per *controller model*, read at
about 28 pixels beside a player's number. A console that already has a diagram
is not drawn twice — the lookup falls back to it, so the two cannot drift.

Nothing here loads an image. What icon a name resolves to is answerable without
a screen, which is what lets it be tested.
"""

from __future__ import annotations

import json
import os
import pathlib

from . import config


def _rules() -> list[tuple[str, str]]:
    """The substring table, in the order it is written in the config.

    A list of one-entry mappings rather than one mapping, because order is the
    whole of it -- "steam virtual" has to be tried before "steam" -- and YAML
    mappings are not something to rely on the ordering of.
    """
    out: list[tuple[str, str]] = []
    for entry in config.get("icons.rules", []) or []:
        if isinstance(entry, dict):
            out.extend((str(k).lower(), str(v)) for k, v in entry.items())
    return out


def _id_rules() -> list[tuple[str, str]]:
    """The `vendor:product` table, for the pads a name cannot identify.

    Exact matches, not substrings: an id is an identifier, which is the whole
    reason these exist beside the name rules rather than among them.
    """
    out: list[tuple[str, str]] = []
    for entry in config.get("icons.ids", []) or []:
        if isinstance(entry, dict):
            out.extend((str(k).lower().strip(), str(v)) for k, v in entry.items())
    return out


def _fallback() -> str:
    """The generic pad. An unknown controller is still a controller, and the
    generic drawing says "a pad is here" -- which is the question asked."""
    return str(config.get("icons.fallback", "generic"))


FALLBACK = "generic"


def icon_name(pad_name: str | None, ids: str | None = None) -> str:
    """The icon a controller asks for, by its ids first and its name after.

    `ids` is `vendor:product` in lowercase hex (`devices.ids_for`), and it wins
    where it is known because a name is what a maker wrote and an id is what a
    device is: a Deck calls itself "Valve Software Steam Controller", exactly
    as a Puck does, and only 28de:1205 says which of the two somebody is
    holding.

    A pad nobody has a rule for either way is not a failure: an unknown
    controller is still a controller, and the generic drawing says "a pad is
    here" — which is the question the strip answers.
    """
    if ids:
        wanted = str(ids).lower().strip()
        for needle, icon in _id_rules():
            if needle == wanted:
                return icon
    lowered = (pad_name or "").lower()
    for needle, icon in _rules():
        if needle in lowered:
            return icon
    return _fallback()


def icons_dir() -> pathlib.Path:
    override = os.environ.get("GOTG_UI_ICONS")
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parent.parent / "assets" / "icons"


def controllers_dir() -> pathlib.Path:
    override = os.environ.get("GOTG_UI_CONTROLLER_ART")
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parent.parent / "assets" / "controllers"


def icon_path(pad_name: str | None, ids: str | None = None) -> pathlib.Path | None:
    """Where that icon's file is, or None if there is no artwork at all.

    Icons first, then the console diagrams: a console drawn once for the
    binding screen is the same picture the strip wants, and vendoring it twice
    would be two copies to de-brand and two to keep in step.
    """
    name = icon_name(pad_name, ids)
    for directory in (icons_dir(), controllers_dir()):
        candidate = directory / f"{name}.svg"
        if candidate.exists():
            return candidate
    fallback = controllers_dir() / f"{_fallback()}.svg"
    return fallback if fallback.exists() else None


def built_dir() -> pathlib.Path:
    """Where the rasterised icons are.

    Beside the console diagrams, under whatever the wrapper set as the built
    assets -- the SVGs themselves never ship, and are never loaded at runtime.
    Same reason as the diagrams: pygame's own SVG support clamps to the source
    aspect ratio, so the size is decided at build time where it can be checked.
    """
    override = os.environ.get("GOTG_UI_ASSETS")
    base = pathlib.Path(override) if override else pathlib.Path(__file__).resolve().parent.parent / "assets" / "built"
    return base / "icons"


def icon_image(pad_name: str | None, ids: str | None = None) -> pathlib.Path | None:
    """The PNG to draw for this controller, or None when none was built.

    The generic pad stands in for anything unrecognised *and* for anything
    whose own drawing is missing: a picture of a controller answers "somebody
    is holding a pad" either way, which is the question the strip asks. None
    only when there are no built icons at all -- a tree nobody has run the
    build in -- and the strip then draws what it drew before.
    """
    directory = built_dir()
    try:
        manifest = json.loads((directory / "icons.json").read_text())
    except (OSError, ValueError):
        return None
    files = manifest.get("icons", {})
    for name in (icon_name(pad_name, ids), _fallback(), FALLBACK):
        filename = files.get(name)
        if filename:
            candidate = directory / str(filename)
            if candidate.exists():
                return candidate
    return None
