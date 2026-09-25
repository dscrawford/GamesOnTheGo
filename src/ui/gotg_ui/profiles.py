"""What each control is actually bound to, read off danstick's own profile.

danstick keeps one file per controller under `~/.local/share/danstick/devices/`,
holding what it measured and what it captured: a `mappings` table keyed by
scope — `""` for the universal one, `console:gamecube` for a console — and
inside each, one binding per control.

Read rather than asked for, because there is no command that answers it: the
daemon reports which *scopes* a pad has a capture under, not what is in them.
Reading the file it wrote is the difference between a screen that can say
"Z is the left trigger" and one that can only say the pad has been mapped.

One binding per control is danstick's model, so that is what this reports. A
second input on the same button — the thing a left-handed player or a broken
shoulder wants — has nowhere to live yet; see docs/requests/.
"""

from __future__ import annotations

import json
import os
import pathlib

# SDL's hat bits, which is how danstick stores a d-pad direction.
HAT = {1: "up", 2: "right", 4: "down", 8: "left"}

UNIVERSAL = ""


def devices_dir() -> pathlib.Path:
    """Where danstick keeps them. Its own rule, followed rather than guessed:
    XDG_DATA_HOME, then the default beneath it."""
    base = os.environ.get("DANSTICK_DEVICES")
    if base:
        return pathlib.Path(base)
    data = os.environ.get("XDG_DATA_HOME") or (pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(data) / "danstick" / "devices"


def load(directory: pathlib.Path | None = None) -> list[dict]:
    """Every profile danstick has written, newest first.

    Newest first because the interesting one is almost always the pad somebody
    has just been using, and a machine accumulates these.
    """
    where = directory if directory is not None else devices_dir()
    found = []
    try:
        files = sorted(where.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            # One unreadable profile is one controller with no detail, not a
            # screen that will not draw.
            continue
        if isinstance(data, dict):
            found.append(data)
    return found


def for_pad(name: str, directory: pathlib.Path | None = None) -> dict | None:
    """The profile for a controller, by the name it reports.

    By name because that is what the daemon's `state` carries for a seated pad.
    danstick keys its own files by signature -- vendor, product and name -- which
    a front end has no other way to know.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for profile in load(directory):
        if str(profile.get("name", "")).strip().lower() == wanted:
            return profile
    return None


def bindings(profile: dict | None, scope: str) -> dict[str, dict]:
    """The bindings for one scope, falling back to the universal capture.

    A pad mapped for GameCube and played on an N64 game is not unmapped: the
    universal capture is what danstick itself falls back to, so a screen that
    showed nothing there would be disagreeing with the thing it is describing.
    """
    if not profile:
        return {}
    mappings = profile.get("mappings") or {}
    for key in (scope, UNIVERSAL):
        entry = mappings.get(key)
        if isinstance(entry, dict):
            buttons = entry.get("buttons")
            if isinstance(buttons, dict) and buttons:
                return buttons
    return {}


def describe(binding: dict | None) -> str:
    """One binding, in words somebody holding the pad could check.

    "Button 3" rather than "b3": the person reading this is looking at a
    controller, not at an SDL mapping string.
    """
    if not isinstance(binding, dict):
        return ""
    kind = binding.get("kind")
    index = binding.get("index")
    value = binding.get("value")
    if kind == "button":
        return f"Button {index}"
    if kind == "hat":
        where = HAT.get(value if isinstance(value, int) else 0, "")
        return f"Hat {where}".strip() if where else f"Hat {index}"
    if kind == "axis":
        way = "+" if isinstance(value, int) and value >= 0 else "−"
        return f"Axis {index}{way}"
    return str(kind or "")


def described(profile: dict | None, scope: str) -> dict[str, str]:
    """Every bound control for a scope, as control -> words."""
    return {control: describe(binding) for control, binding in bindings(profile, scope).items()}
