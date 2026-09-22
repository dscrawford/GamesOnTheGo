"""What the pad is bound to, read from the tables that do the binding.

Nothing here decides a mapping. `gotg pads` writes ares' settings.bml from
src/client/data/ares-pads.json, and this reads that same file, so the screen
shows what will actually be applied rather than a second description of it that
could drift. If a binding looks wrong here it is wrong in the emulator too, and
the fix is one file for both.

Which console a platform is depends on its environment — env/genesis.nix says
`console = "MegaDrive"` — and the built environment publishes that in
share/gotg/pads.json, which the client keeps a GC root to. That root is read
directly rather than through `gotg`, because the alternative is asking nix, and
nix's answer to a platform nobody has played yet is a twenty-minute build. A
platform not built yet gets no diagram, which is a screen that says so instead
of a screen that guesses.
"""

from __future__ import annotations

import json
import os
import pathlib

# SDL's names for the standard elements, in the words a person would use. The
# table's right-hand side is SDL, because that is the indirection that lets one
# table serve every pad; this is only for showing it.
ELEMENT_NAMES = {
    "a": "A button",
    "b": "B button",
    "x": "X button",
    "y": "Y button",
    "back": "Back",
    "guide": "Guide",
    "start": "Start",
    "leftshoulder": "Left bumper",
    "rightshoulder": "Right bumper",
    "lefttrigger": "Left trigger",
    "righttrigger": "Right trigger",
    "leftstick": "Left stick (click)",
    "rightstick": "Right stick (click)",
    "dpup": "D-pad up",
    "dpdown": "D-pad down",
    "dpleft": "D-pad left",
    "dpright": "D-pad right",
    "leftx": "Left stick",
    "lefty": "Left stick",
    "rightx": "Right stick",
    "righty": "Right stick",
}

# A direction on an axis. The sign is the low or high side, which the table
# spells with a trailing - or +.
AXIS_DIRECTION = {
    ("leftx", "-"): "Left stick left",
    ("leftx", "+"): "Left stick right",
    ("lefty", "-"): "Left stick up",
    ("lefty", "+"): "Left stick down",
    ("rightx", "-"): "Right stick left",
    ("rightx", "+"): "Right stick right",
    ("righty", "-"): "Right stick up",
    ("righty", "+"): "Right stick down",
}


# padmap's own control ids, where they differ from the names this table uses.
# padmap spells a stick direction `leftstick_left`; ares' table spells the same
# thing `leftx-`. Nothing else disagrees -- both sides took their button names
# from SDL -- but these eight did, and the mismatch is why a press lit nothing:
# a profile answered `rightstick_up` and every label on the drawing was called
# `C-Up`.
PADMAP_ELEMENTS = {
    "leftstick_left": "leftx-",
    "leftstick_right": "leftx+",
    "leftstick_up": "lefty-",
    "leftstick_down": "lefty+",
    "rightstick_left": "rightx-",
    "rightstick_right": "rightx+",
    "rightstick_up": "righty-",
    "rightstick_down": "righty+",
}


def pad_controls(console: str | None) -> dict[str, str]:
    """padmap's control id -> this console's own name for it.

    The bridge between what a profile says somebody pressed (`dpup`) and what
    the drawing calls it (`Up`), read off the same table that binds them, so
    the two cannot disagree. Several console inputs can share an element --
    the stick doubling as the D-pad -- and the first in the table wins, since
    it is the one the label is for.

    Empty for a console this table has never heard of, which is Dolphin's two:
    there the drawing is labelled with padmap's own ids already.
    """
    table = (ares_table().get(console or "") or {}).get("buttons") or {}
    out: dict[str, str] = {}
    for control, value in table.items():
        for element in value if isinstance(value, list) else [value]:
            out.setdefault(str(element), control)
    for padmap_id, element in PADMAP_ELEMENTS.items():
        if element in out:
            out.setdefault(padmap_id, out[element])
    return out


def describe(element: str) -> str:
    """One SDL element, in words."""
    if element and element[-1] in "-+":
        base, sign = element[:-1], element[-1]
        named = AXIS_DIRECTION.get((base, sign))
        if named:
            return named
        return f"{ELEMENT_NAMES.get(base, base)} {'-' if sign == '-' else '+'}"
    return ELEMENT_NAMES.get(element, element)


def describe_all(value) -> str:
    """What one ares input is driven by.

    A list means ares holds several bindings for the input and any of them
    works — the stick doubling as the D-pad is the usual case — so they are
    joined rather than one being picked.
    """
    if isinstance(value, list):
        seen = list(dict.fromkeys(describe(v) for v in value))
        return " / ".join(seen)
    return describe(value)


def data_dir() -> pathlib.Path:
    """Where the client keeps its tables.

    GOTG_DATA when the client exported it, which is the case for anything the
    client itself started; otherwise the wrapper's own pointer.
    """
    for variable in ("GOTG_DATA", "GOTG_UI_DATA"):
        value = os.environ.get(variable)
        if value:
            return pathlib.Path(value)
    return pathlib.Path("/nonexistent")


def ares_table() -> dict:
    """The console -> bindings table, or empty when it cannot be read."""
    try:
        return json.loads((data_dir() / "ares-pads.json").read_text())
    except (OSError, ValueError):
        return {}


def roots_dir() -> pathlib.Path:
    """Where the client keeps its built-environment GC roots.

    The same path env.sh computes, from the same variables, so a client
    configured onto another disk is followed rather than missed.
    """
    state = os.environ.get("GOTG_STATE_DIR")
    if state:
        return pathlib.Path(state) / "roots"
    base = os.environ.get("XDG_STATE_HOME") or (pathlib.Path.home() / ".local" / "state")
    return pathlib.Path(base) / "gotg" / "env" / ".." / "roots"


def console_for(platform: str) -> str | None:
    """Which console a platform's environment says it is, or None.

    None covers both "not built yet" and "this emulator's bindings are not
    generated" — dolphin and Ryujinx write theirs elsewhere and publish no
    console — and the screen says so either way rather than inventing one.
    """
    try:
        manifest = roots_dir().resolve() / f"env-{platform}" / "share" / "gotg" / "pads.json"
        return json.loads(manifest.read_text()).get("console") or None
    except (OSError, ValueError):
        return None


def bindings_for(console: str, table: dict | None = None) -> dict[str, str]:
    """ares input -> what drives it, for one console.

    Keyed by the ares input name, which is what an anchor in the SVG is named
    after, so the diagram and this line up without a third table in between.
    """
    table = ares_table() if table is None else table
    entry = table.get(console) or {}
    buttons = entry.get("buttons") or {}
    return {name: describe_all(value) for name, value in buttons.items()}


def players_for(console: str, table: dict | None = None) -> int:
    """How many pads this console seats."""
    table = ares_table() if table is None else table
    return int(((table.get(console) or {}).get("layout") or {}).get("players", 1))
