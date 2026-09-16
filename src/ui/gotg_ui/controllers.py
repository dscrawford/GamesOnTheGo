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
from .padstrip import (
    EMPTY,
    EMPTY_RING,
    EMPTY_TEXT,
    GAP,
    HEIGHT,
    LABEL,
    LABEL_DIM,
    PAD_RADIUS,
    PANEL,
    colour_for,
    name_for,
    seats,
)

BACKGROUND = (18, 18, 20)
TEXT = (232, 232, 236)
TEXT_DIM = (150, 150, 158)
LEADER = (108, 108, 122)
LEADER_LIT = (120, 180, 240)
DOT = (150, 150, 162)

# Which drawing stands for which console. A console absent here falls back to
# the generic pad, which is what a new platform gets until someone draws it.
# See assets/controllers/README.md for where the artwork came from and how to
# add another.
TABLE = {
    "SuperFamicom": "snes",
    "Famicom": "nes",
    "Nintendo64": "n64",
    "GameBoy": "gameboy",
    # The Color shares the Pocket's shape and its whole button set. A drawing
    # of its own would differ only in the shell colour, which the diagram does
    # not depend on.
    "GameBoyColor": "gameboy",
    "GameBoyAdvance": "gba",
    "MegaDrive": "megadrive",
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
            # aalines and aacircle: a leader is a thin diagonal, which is the
            # shape aliasing shows up on worst, and it is drawn over artwork
            # that resvg already anti-aliased.
            pygame.draw.aalines(screen, colour, False, [(int(x), int(y)) for x, y in item.points], 2)
        pygame.draw.aacircle(screen, colour if lit else DOT, (int(item.points[0][0]), int(item.points[0][1])), 4)

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

    # Said out loud rather than left to be noticed: a diagram that quietly drew
    # twenty-one of twenty-two would read as a complete answer.
    #
    # Not always a gap to close, either. Every pad here is drawn from the front,
    # so the N64's Z sits under the grip and has nowhere to point at — it is
    # bound, it works, and this line is the only place that says so. A console
    # still on the generic pad has a larger number and that one *is* a to-do.
    missing = len(bindings) - len(anchors)
    if missing:
        note = f"{missing} more bound, with nowhere on this drawing to point at"
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


def draw_strip(screen, font_at, players: list[dict], slots: int, status: str) -> int:
    """Draw the strip along the top. Returns the height it used.

    The caller offsets everything below by that, so the strip decides its own
    height and the screens under it do not carry a copy of the number.
    """
    width = screen.get_width()
    pygame.draw.rect(screen, PANEL, pygame.Rect(0, 0, width, HEIGHT))

    small = font_at(15)
    tiny = font_at(12)

    x = GAP + PAD_RADIUS
    middle = HEIGHT // 2
    occupied = seats(players, slots)

    if not occupied:
        # Nothing is connected, and that is worth saying once rather than as a
        # row of empty rings. An X is the only thing on the strip that means
        # "none", so it cannot be read as a seat.
        reach = PAD_RADIUS - 4
        for dx, dy in ((-1, -1), (-1, 1)):
            pygame.draw.aaline(
                screen,
                EMPTY_RING,
                (x + dx * reach, middle + dy * reach),
                (x - dx * reach, middle - dy * reach),
                2,
            )
        label = tiny.render("no controllers", True, EMPTY_TEXT)
        screen.blit(label, (x + PAD_RADIUS + 6, middle - label.get_height() // 2))
    for player, seat in occupied:
        colour = colour_for(player)
        # aacircle, not circle: a hard-edged disc at this size is visibly
        # stepped, and a row of them across the top of every screen is the
        # first thing anybody notices about the strip.
        pygame.draw.aacircle(screen, colour, (x, middle), PAD_RADIUS)

        number = small.render(str(player), True, (20, 20, 24))
        screen.blit(number, number.get_rect(center=(x, middle)))

        written = name_for(seat)
        width_used = 0
        if written:
            label = tiny.render(written, True, LABEL)
            screen.blit(label, (x + PAD_RADIUS + 6, middle - label.get_height() // 2))
            width_used = 6 + label.get_width()
        x += PAD_RADIUS * 2 + width_used + GAP * 2

    word = tiny.render(status, True, LABEL_DIM)
    screen.blit(word, (width - word.get_width() - GAP, middle - word.get_height() // 2))
    return HEIGHT


def draw_assign(screen, font_at, view, icon_surface=None) -> None:
    """The assignment screen: what to hold, what has been taken, what is left.

    Deliberately large and plain. It is read from a sofa by somebody holding a
    controller in both hands, and every line on it is an instruction.
    """
    width, height = screen.get_size()
    screen.fill(BACKGROUND)

    title = font_at(46).render("Controllers", True, TEXT)
    screen.blit(title, ((width - title.get_width()) // 2, int(height * 0.06)))

    prompt = font_at(30).render(view.prompt, True, TEXT)
    screen.blit(prompt, ((width - prompt.get_width()) // 2, int(height * 0.20)))

    # The hold in flight. padmap reports it as a fraction, and a bar is the
    # only part of this screen that answers "is it registering my button?"
    if view.progress > 0:
        bar_w = int(width * 0.4)
        bar_x = (width - bar_w) // 2
        bar_y = int(height * 0.30)
        pygame.draw.rect(screen, LEADER, pygame.Rect(bar_x, bar_y, bar_w, 10), border_radius=5)
        pygame.draw.rect(
            screen,
            LEADER_LIT,
            pygame.Rect(bar_x, bar_y, int(bar_w * min(1.0, view.progress)), 10),
            border_radius=5,
        )

    # The seats, across the middle, in the same colours the strip uses.
    from .padstrip import EMPTY, EMPTY_TEXT, colour_for

    slot_w = min(200, width // max(1, view.slots))
    total = slot_w * view.slots
    left = (width - total) // 2
    middle = int(height * 0.52)
    taken = {seat.player: seat for seat in view.seats}
    for index in range(view.slots):
        player = index + 1
        seat = taken.get(player)
        centre = (left + index * slot_w + slot_w // 2, middle)
        colour = colour_for(player) if seat else EMPTY
        pygame.draw.aacircle(screen, colour, centre, 34)
        if seat is None:
            pygame.draw.aacircle(screen, EMPTY_RING, centre, 34, 3)
        number = font_at(38).render(str(player), True, (20, 20, 24) if seat else EMPTY_TEXT)
        screen.blit(number, number.get_rect(center=centre))

        label = font_at(20).render(
            (seat.name if seat else "waiting")[:22], True, TEXT if seat else EMPTY_TEXT
        )
        screen.blit(label, label.get_rect(center=(centre[0], middle + 56)))

    if view.message:
        note = font_at(22).render(view.message, True, (232, 140, 140))
        screen.blit(note, ((width - note.get_width()) // 2, int(height * 0.72)))

    keys = "Enter keep   R start again   Esc cancel" if view.state == "assigning" \
        else "A or Enter assign   Esc back"
    footer = font_at(20).render(keys, True, TEXT_DIM)
    screen.blit(footer, ((width - footer.get_width()) // 2, int(height * 0.86)))
