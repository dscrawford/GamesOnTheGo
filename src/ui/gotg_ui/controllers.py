"""Drawing the controller diagram: the pad, the labels, and the lines between.

The only part of this feature that needs a screen. Where the labels go is
leaders.py's, what they say is bindings.py's, and both are answerable without a
display — what is left here is blitting a PNG and stroking some polylines.

The artwork is a PNG chosen from the density tiers build-controllers.py wrote,
never an SVG: pygame's SVG support clamps to the source aspect ratio, so a
diagram sized from the request rather than the result would put every leader a
few percent off the button it points at.
"""

from __future__ import annotations

import json
import os
import pathlib

import pygame

from .bindings import bindings_for, console_for, players_for
from .leaders import Anchor, place

BACKGROUND = (18, 18, 20)
TEXT = (232, 232, 236)
TEXT_DIM = (150, 150, 158)
LEADER = (108, 108, 122)
LEADER_LIT = (120, 180, 240)
DOT = (150, 150, 162)

# Which SVG stands for which console. Every console points at the placeholder
# for now; a real drawing arrives by being named here, and nothing else moves.
TABLE = {
    "SuperFamicom": "generic",
    "Famicom": "generic",
    "Nintendo64": "generic",
    "GameBoy": "generic",
    "GameBoyColor": "generic",
    "GameBoyAdvance": "generic",
    "MegaDrive": "generic",
}
FALLBACK = "generic"

# The shoulders sit near the top centre, where a midline split would send them
# to whichever side they happen to land on and run the leader diagonally across
# the pad. Named instead. Keyed by ares input name.
PINNED = {"L": "left", "R": "right", "Z": "right", "C": "right"}


def assets_dir() -> pathlib.Path:
    """Where the built artwork and its manifest are.

    The wrapper sets this; a checkout run in place falls back to the build
    script's own output directory, so `python -m gotg_ui` from the tree works
    once build-controllers.py has been run there.
    """
    override = os.environ.get("GOTG_UI_ASSETS")
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parent.parent / "assets" / "built"


class Diagram:
    """One controller's artwork and anchors, loaded once."""

    def __init__(self, assets: pathlib.Path, name: str):
        self.name = name
        manifest = json.loads((assets / "controllers.json").read_text())
        entry = manifest["controllers"][name]
        self.size = tuple(entry["size"])
        self.anchors: dict[str, tuple[float, float]] = {k: tuple(v) for k, v in entry["anchors"].items()}
        self._images = {int(d): assets / f for d, f in entry["images"].items()}
        self._cache: dict[int, pygame.Surface] = {}

    def surface(self, width: int) -> pygame.Surface:
        """The pad at `width` pixels, from the smallest tier big enough.

        Scaling down keeps the edges; scaling a 1x up to a television does not.
        The largest tier is the ceiling — past that it stretches, which is the
        honest failure and beats refusing to draw.
        """
        tiers = sorted(self._images)
        want = next((t for t in tiers if self.size[0] * t >= width), tiers[-1])
        if want not in self._cache:
            self._cache[want] = pygame.image.load(str(self._images[want])).convert_alpha()
        source = self._cache[want]
        height = round(width * self.size[1] / self.size[0])
        if source.get_size() == (width, height):
            return source
        return pygame.transform.smoothscale(source, (width, height))


def diagram_for(assets: pathlib.Path, console: str, cache: dict) -> Diagram | None:
    """The drawing for one console, or None when the assets are unreadable."""
    name = TABLE.get(console, FALLBACK)
    if name not in cache:
        try:
            cache[name] = Diagram(assets, name)
        except (OSError, ValueError, KeyError):
            cache[name] = None
    return cache[name]


def anchors_for(diagram: Diagram, bindings: dict[str, str], rect) -> list[Anchor]:
    """Every bound input that the artwork has a place for.

    The intersection on purpose, and it fails in the safe direction both ways:
    an input the table binds but this drawing has no anchor for is left out
    rather than pointed at the middle of the pad, and an anchor with no binding
    draws nothing rather than an empty label. A placeholder shared by seven
    consoles is exactly the case where both happen.
    """
    left, top, width, height = rect
    out = []
    for name, description in bindings.items():
        uv = diagram.anchors.get(name)
        if uv is None:
            continue
        out.append(Anchor(input=name, x=left + uv[0] * width, y=top + uv[1] * height, label=f"{name} — {description}"))
    return out


def draw(screen, assets: pathlib.Path, platform: str, font_at, cache: dict, highlight: str | None = None) -> None:
    """The whole screen: pad, labels, leaders, and whatever is missing."""
    width, height = screen.get_size()
    screen.fill(BACKGROUND)

    title = font_at(46).render(f"Controller — {platform}", True, TEXT)
    screen.blit(title, ((width - title.get_width()) // 2, int(height * 0.045)))

    console = console_for(platform)
    if console is None:
        _note(
            screen,
            font_at,
            "No bindings for this platform yet.",
            f"Play a {platform} game once and they appear here.",
        )
        return

    bindings = bindings_for(console)
    diagram = diagram_for(assets, console, cache)
    if diagram is None or not bindings:
        _note(
            screen,
            font_at,
            f"{console}: nothing to draw.",
            "The controller artwork or the pad table could not be read.",
        )
        return

    # The pad, centred, with room either side for a rail of labels and above
    # for the title. Fitted to whichever axis runs out first, the way the tile
    # grid is: a wide window should not leave the diagram stranded in the
    # middle of its own empty space.
    label_font = font_at(max(15, int(height * 0.028)))
    aspect = diagram.size[1] / diagram.size[0]
    pad_width = int(min(width * 0.46, height * 0.52 / aspect))
    pad_height = round(pad_width * aspect)
    rect = ((width - pad_width) / 2, (height - pad_height) / 2 + height * 0.02, pad_width, pad_height)

    art = diagram.surface(pad_width)
    screen.blit(art, (int(rect[0]), int(rect[1])))

    anchors = anchors_for(diagram, bindings, rect)
    label_height = label_font.get_linesize()
    for item in place(anchors, rect, label_height, PINNED):
        lit = highlight is not None and item.anchor.input == highlight
        colour = LEADER_LIT if lit else LEADER
        pygame.draw.aalines(screen, colour, False, [(x, y) for x, y in item.points])
        if lit:
            pygame.draw.lines(screen, colour, False, [(int(x), int(y)) for x, y in item.points], 2)
        pygame.draw.circle(screen, colour if lit else DOT, (int(item.points[0][0]), int(item.points[0][1])), 4)

        text = label_font.render(item.anchor.label, True, TEXT if lit else TEXT_DIM)
        x = item.x + 8 if item.align == "left" else item.x - text.get_width() - 8
        screen.blit(text, (int(x), int(item.y)))

    seats = players_for(console)
    line = (
        f"{console}  ·  {len(anchors)} of {len(bindings)} inputs  ·  "
        f"{seats} player{'s' if seats != 1 else ''}   —   applied to every game on this platform"
    )
    screen.blit(
        font_at(24).render(line, True, TEXT_DIM),
        ((width - font_at(24).size(line)[0]) // 2, int(height * 0.93)),
    )

    # Said out loud rather than left to be noticed. While every console shares
    # one placeholder pad there are bindings it has nowhere to point at — the
    # N64's C buttons, a stick's axes — and a diagram that quietly showed ten
    # of twenty-two would read as a complete answer. This is also the to-do:
    # the number goes to zero when that console gets its own drawing.
    missing = len(bindings) - len(anchors)
    if missing:
        note = f"{missing} more bound, with nowhere on this placeholder pad to show them"
        screen.blit(
            font_at(21).render(note, True, LEADER),
            ((width - font_at(21).size(note)[0]) // 2, int(height * 0.965)),
        )


def _note(screen, font_at, headline: str, detail: str) -> None:
    width, height = screen.get_size()
    one = font_at(34).render(headline, True, TEXT_DIM)
    two = font_at(24).render(detail, True, TEXT_DIM)
    screen.blit(one, ((width - one.get_width()) // 2, height // 2 - one.get_height()))
    screen.blit(two, ((width - two.get_width()) // 2, height // 2 + 10))
