"""What each controller is, read from `config/controllers/`.

One file per controller, not per platform: a Wii game is played with a GameCube
pad here, and duplicating sixteen controls into a second file is two places to
fix a typo. Each file says which platforms it covers, which padmap layout the
capture walks, which drawing stands for it, and what every control is called.

The point of it being a file is that adding a console is a data edit. Nothing
in this directory knows that a GameCube has a Z button or that a SNES calls its
bottom face button B; if padmap grows a layout, or somebody draws a Switch Pro
pad, the change is a `.yaml` and nothing else.

The control names mirror padmap's own layouts, because the capture walks them
and the screen labels them and those two disagreeing is a step that points at
the wrong button. `tests/ui/test_schemes.py` checks them against padmap's data.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import yaml

FALLBACK = "generic"


@dataclass(frozen=True)
class Scheme:
    """One controller, as the config describes it."""

    name: str
    label: str = ""
    layout: str = FALLBACK
    artwork: str = FALLBACK
    players: int = 1
    # What ares calls this console. A list: the Game Boy and the Game Boy
    # Color are two ares consoles and one controller.
    ares: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    controls: dict[str, str] = field(default_factory=dict)
    # control -> the anchor in the artwork that marks it, where the drawing
    # does not name its circles after padmap's controls.
    anchors: dict[str, str] = field(default_factory=dict)

    def anchor_names(self, control: str) -> tuple[str, ...]:
        """Which anchors could mark this control, best first."""
        if not control:
            return ()
        mapped = self.anchors.get(control)
        return (control, mapped) if mapped else (control,)


def config_dir() -> pathlib.Path:
    """Where the controller files are.

    The wrapper sets this. A checkout run in place falls back to the tree, so
    `python -m gotg_ui` from src/ui finds them with nothing installed.
    """
    override = os.environ.get("GOTG_UI_CONFIG")
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parents[3] / "config" / "controllers"


def _read(path: pathlib.Path) -> Scheme | None:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        # One unreadable file is one controller without a description, not a
        # picker that will not start.
        return None
    if not isinstance(raw, dict):
        return None
    controls = raw.get("controls") or {}
    anchors = raw.get("anchors") or {}
    platforms = raw.get("platforms") or []
    return Scheme(
        name=path.stem,
        label=str(raw.get("label") or path.stem),
        layout=str(raw.get("layout") or FALLBACK),
        artwork=str(raw.get("artwork") or FALLBACK),
        players=int(raw.get("players") or 1),
        ares=tuple(str(a) for a in (raw.get("ares") or [])),
        platforms=tuple(str(p) for p in platforms),
        controls={str(k): str(v) for k, v in controls.items()} if isinstance(controls, dict) else {},
        anchors={str(k): str(v) for k, v in anchors.items()} if isinstance(anchors, dict) else {},
    )


_cache: dict[str, Scheme] | None = None


def load(directory: pathlib.Path | None = None) -> dict[str, Scheme]:
    """Every controller, by file name. Read once."""
    global _cache
    if directory is None and _cache is not None:
        return _cache
    where = directory if directory is not None else config_dir()
    found: dict[str, Scheme] = {}
    try:
        files = sorted(where.glob("*.yaml"))
    except OSError:
        files = []
    for path in files:
        scheme = _read(path)
        if scheme is not None:
            found[scheme.name] = scheme
    if directory is None:
        _cache = found
    return found


def forget() -> None:
    """Drop the cache. For tests, and for a config edit taking effect."""
    global _cache
    _cache = None


def fallback() -> Scheme:
    """The controller a platform nobody claims is played with.

    A Scheme rather than None, so every caller has something to draw. An empty
    one when the config is missing entirely -- which is a screen that says a
    console has no controls, rather than a traceback.
    """
    return load().get(FALLBACK) or Scheme(name=FALLBACK)


def for_platform(platform: str) -> Scheme:
    """The controller a GOTG platform is played with."""
    wanted = (platform or "").lower()
    for scheme in load().values():
        if wanted in scheme.platforms:
            return scheme
    return fallback()


def for_ares(console: str) -> Scheme | None:
    """The controller an ares console name means, or None.

    ares names its consoles its own way -- SuperFamicom, Nintendo64 -- and the
    bindings it writes are keyed by those, so the binding screen arrives
    holding one of them rather than a platform.
    """
    for scheme in load().values():
        if console in scheme.ares:
            return scheme
    return None
