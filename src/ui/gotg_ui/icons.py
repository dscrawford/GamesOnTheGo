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

import os
import pathlib

# Substrings, most specific first, because a pad reports whatever its maker
# wrote: "Xbox 360 Controller" and "Microsoft X-Box 360 pad" are one icon. The
# order matters -- Steam's virtual pad is an Xbox pad wearing a different hat.
RULES: list[tuple[str, str]] = [
    ("steam virtual", "xbox"),
    ("steam controller", "steam"),
    ("steam deck", "steam"),
    ("valve", "steam"),
    ("gamecube", "gamecube"),
    ("nintendo gamecube", "gamecube"),
    ("wii u", "generic"),
    ("wii remote", "wii"),
    ("wiimote", "wii"),
    ("joy-con", "switch"),
    ("joycon", "switch"),
    ("switch pro", "switch"),
    ("nintendo switch", "switch"),
    ("nintendo 64", "n64"),
    ("n64", "n64"),
    ("super nintendo", "snes"),
    ("snes", "snes"),
    ("famicom", "nes"),
    ("nes ", "nes"),
    ("mega drive", "megadrive"),
    ("megadrive", "megadrive"),
    ("genesis", "megadrive"),
    ("game boy advance", "gba"),
    ("gba", "gba"),
    ("game boy", "gameboy"),
    ("gameboy", "gameboy"),
    ("dualshock", "playstation"),
    ("dualsense", "playstation"),
    ("playstation", "playstation"),
    ("ps3", "playstation"),
    ("ps4", "playstation"),
    ("ps5", "playstation"),
    ("xbox", "xbox"),
    ("x-box", "xbox"),
    ("x360", "xbox"),
]

FALLBACK = "generic"


def icon_name(pad_name: str | None) -> str:
    """The icon a controller's name asks for, or the generic pad.

    A name nobody has a rule for is not a failure: an unknown controller is
    still a controller, and the generic drawing says "a pad is here" — which is
    the question the strip answers.
    """
    lowered = (pad_name or "").lower()
    for needle, icon in RULES:
        if needle in lowered:
            return icon
    return FALLBACK


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


def icon_path(pad_name: str | None) -> pathlib.Path | None:
    """Where that icon's file is, or None if there is no artwork at all.

    Icons first, then the console diagrams: a console drawn once for the
    binding screen is the same picture the strip wants, and vendoring it twice
    would be two copies to de-brand and two to keep in step.
    """
    name = icon_name(pad_name)
    for directory in (icons_dir(), controllers_dir()):
        candidate = directory / f"{name}.svg"
        if candidate.exists():
            return candidate
    fallback = controllers_dir() / f"{FALLBACK}.svg"
    return fallback if fallback.exists() else None
