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
import math
import os
import pathlib
from dataclasses import dataclass

import pygame

from . import config, icons
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
    next_seat,
    seats,
    wedge,
)
from .schemes import for_ares, for_platform

BACKGROUND = config.colour("theme.colours.background", (18, 18, 20))
TEXT = config.colour("theme.colours.text", (232, 232, 236))
TEXT_DIM = config.colour("theme.colours.text_dim", (150, 150, 158))
LEADER = config.colour("theme.colours.leader", (108, 108, 122))
LEADER_LIT = config.colour("theme.colours.leader_lit", (120, 180, 240))
DOT = config.colour("theme.colours.dot", (150, 150, 162))

# Which drawing stands for which console. A console absent here falls back to
# the generic pad, which is what a new platform gets until someone draws it.
# See assets/controllers/README.md for where the artwork came from and how to
# add another.
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


def diagram_for(assets: pathlib.Path, name: str, cache: dict) -> Diagram | None:
    """One drawing by name, or None when the assets are unreadable.

    Named outright rather than worked out from a console: which drawing stands
    for which controller is `config/controllers/`'s business, and the caller
    has already read it.
    """
    name = name or FALLBACK
    if name not in cache:
        try:
            cache[name] = Diagram(assets, name)
        except (OSError, ValueError, KeyError):
            cache[name] = None
    return cache[name]


def anchors_for(
    diagram: Diagram,
    bindings: dict[str, str],
    rect,
    with_binding: bool = True,
    alias: dict[str, str] | None = None,
    pressed: dict[str, str] | None = None,
) -> list[Anchor]:
    """Every bound input that the artwork has a place for.

    The intersection on purpose, and it fails in the safe direction both ways:
    an input the table binds but this drawing has no anchor for is left out
    rather than pointed at the middle of the pad, and an anchor with no binding
    draws nothing rather than an empty label. A placeholder shared by seven
    consoles is exactly the case where both happen.
    """
    left, top, width, height = rect
    alias = alias or {}
    out = []
    for name, description in bindings.items():
        # The control's own name first, then whatever the config says marks it:
        # the older drawings name their circles after the console's letters, so
        # a SNES `a` is the circle called B.
        uv = diagram.anchors.get(name)
        if uv is None and name in alias:
            uv = diagram.anchors.get(alias[name])
        if uv is None:
            continue
        # With a binding, the label is the input and what drives it. Without
        # one it is what the button is called, because "dpup — " reads as a
        # line somebody forgot to finish.
        label = f"{name} — {description}" if with_binding else description or name
        actual = (pressed or {}).get(name)
        if actual:
            label = f"{label}   {actual}"
        out.append(Anchor(input=name, x=left + uv[0] * width, y=top + uv[1] * height, label=label))
    return out


@dataclass(frozen=True)
class Shown:
    """What a platform's diagram is made of, resolved once.

    draw() and the cursor both need it, and two answers to "which controls does
    this console have" is two places for them to disagree about what is on
    screen.
    """

    console: str
    bindings: dict
    artwork: str
    seats: int
    with_binding: bool
    alias: dict


def resolve(platform: str) -> Shown:
    console = console_for(platform)
    if console is not None:
        bindings = bindings_for(console)
        known = for_ares(console)
        artwork = known.artwork if known else FALLBACK
        seats = players_for(console)
        return Shown(console, bindings, artwork, seats, True, {})
    else:
        # No ares console. For most platforms that means the environment has
        # not been built yet and playing a game once fills it in -- but for
        # Dolphin's two it means never, because Dolphin writes its own
        # configuration and publishes no console at all. Telling somebody with
        # a GameCube game to play it once and come back is an instruction that
        # cannot work, so padmap's control set is used instead: it is the same
        # one the launch wizard walks, which is where these get bound.
        scheme = for_platform(platform)
        console = scheme.label or scheme.name
        bindings = dict(scheme.controls)
        artwork = scheme.artwork
        # What the controller seats, from its own file. The ares table has
        # never heard of this console and would answer one for a pad that
        # takes four.
        return Shown(console, bindings, artwork, scheme.players, False, scheme.anchors)



def control_places(assets: pathlib.Path, platform: str, cache: dict) -> dict[str, tuple[float, float]]:
    """Every control's place on the drawing, normalised, for moving around it.

    The intersection of what the console has and what the artwork marks, which
    is the same set the labels are drawn from -- a cursor that could land on a
    control with no circle would vanish.
    """
    shown = resolve(platform)
    diagram = diagram_for(assets, shown.artwork, cache)
    if diagram is None:
        return {}
    places = {}
    for name in shown.bindings:
        for candidate in (name, shown.alias.get(name)):
            if candidate and candidate in diagram.anchors:
                places[name] = diagram.anchors[candidate]
                break
    return places


def draw(
    screen,
    assets: pathlib.Path,
    platform: str,
    font_at,
    cache: dict,
    highlight: str | None = None,
    bound: dict[str, str] | None = None,
) -> None:
    """The whole screen: pad, labels, leaders, and whatever is missing.

    `bound` is what each control is actually bound to on the controller in
    hand, read off padmap's profile. Without it the screen can say a console
    has a Z button; with it, it can say which button Z is.
    """
    width, height = screen.get_size()
    screen.fill(BACKGROUND)

    bound_map = bound or {}
    title = font_at(46).render(f"Controller — {platform}", True, TEXT)
    screen.blit(title, ((width - title.get_width()) // 2, int(height * 0.045)))

    shown = resolve(platform)
    console, bindings = shown.console, shown.bindings
    artwork, seats = shown.artwork, shown.seats
    bound, alias = shown.with_binding, shown.alias
    if not bindings:
        # Not "play one and they appear here", which is only true for the
        # emulators that publish a console. Dolphin and Ryujinx never do, so
        # for their platforms that sentence promises something that cannot
        # happen. Starting a game is what captures them, either way.
        _note(
            screen,
            font_at,
            "No bindings for this platform yet.",
            "They are captured the first time you start a game.",
        )
        return

    diagram = diagram_for(assets, artwork, cache)
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

    anchors = anchors_for(diagram, bindings, rect, with_binding=bound, alias=alias, pressed=bound_map)
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

    line = (
        f"{console}  ·  {len(anchors)} of {len(bindings)} inputs  ·  "
        f"{seats} player{'s' if seats != 1 else ''}   —   applied to every game on this platform"
    )
    screen.blit(
        font_at(24).render(line, True, TEXT_DIM),
        ((width - font_at(24).size(line)[0]) // 2, int(height * 0.93)),
    )

    # Said, because a cursor that moves is not obviously a cursor that can be
    # moved. Nothing about adding a second input: padmap holds one binding per
    # control, so offering it would be offering something with nowhere to go.
    keys = font_at(20).render("d-pad or arrows to move around the pad", True, TEXT_DIM)
    screen.blit(keys, ((width - keys.get_width()) // 2, int(height * 0.055)))

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


# Tinted icons, by (file, height, colour). A strip is redrawn every frame and
# these change only when somebody picks up a different controller.
_icons: dict[tuple[str, int, tuple[int, int, int]], object] = {}


def icon_surface(pad_name: str | None, height: int, colour: tuple[int, int, int]):
    """The drawing of this controller, `height` tall, in one colour.

    The artwork is a black silhouette, which on a dark strip is a black
    rectangle's worth of nothing. Adding the colour rather than multiplying it
    keeps the anti-aliased edge -- black plus the colour is the colour, and the
    alpha channel is left alone -- where a mask would have to pick a threshold
    and would show it at 28 pixels.

    In the player's own colour, because that is already how a seat is read:
    the number says which player and the colour is what makes it answerable
    from a sofa without reading anything.
    """
    path = icons.icon_image(pad_name)
    if path is None:
        return None
    key = (str(path), height, colour)
    if key not in _icons:
        source = pygame.image.load(str(path)).convert_alpha()
        width = max(1, round(source.get_width() * height / source.get_height()))
        scaled = pygame.transform.smoothscale(source, (width, height))
        # Flattened to a silhouette first. Not every drawing is pure black --
        # the keyboard is grey, one pad has coloured buttons -- and adding a
        # colour to those washes them toward white while the black ones come
        # out saturated, so a row of seats would not look like one set.
        # Multiplying by black zeroes the colour and leaves the alpha, which is
        # the anti-aliased edge worth keeping.
        scaled.fill((0, 0, 0), special_flags=pygame.BLEND_RGB_MULT)
        scaled.fill(colour, special_flags=pygame.BLEND_RGB_ADD)
        _icons[key] = scaled
    return _icons[key]


def draw_strip(
    screen, font_at, players: list[dict], slots: int, status: str,
    progress: float = 0.0, joining: str | None = None,
) -> int:
    """Draw the strip along the top. Returns the height it used.

    The caller offsets everything below by that, so the strip decides its own
    height and the screens under it do not carry a copy of the number.

    `progress` is a hold in flight, as a fraction, and `joining` is which
    drawing is being revealed by it -- None for the generic pad, "keyboard"
    for the space bar. It is drawn here rather than only on the assignment
    screen because that is where the hold happens: somebody picks a
    controller up in front of the library and holds a button, and the pad
    filling in above the games is the only thing that says the machine
    noticed.
    """
    width = screen.get_width()
    pygame.draw.rect(screen, PANEL, pygame.Rect(0, 0, width, HEIGHT))

    small = font_at(15)
    tiny = font_at(12)

    x = GAP + PAD_RADIUS
    middle = HEIGHT // 2
    occupied = seats(players, slots)

    icon_height = HEIGHT - GAP * 2

    # Where the next badge would go, which is where a hold in flight is drawn.
    # With nobody seated that is over the generic pad on the left; with two
    # seated it is after the second, which is where the third will appear.
    joining_at = x

    if not occupied:
        # Nothing is connected: the generic pad, in the colour that means
        # "something for you to do", rather than a row of empty rings. Drawn
        # rather than written for the same reason as the seats -- a picture of
        # a controller is read from a sofa and "no controllers" is not.
        icon = icon_surface(None, icon_height, EMPTY_RING)
        if icon is not None:
            # Not while a keyboard is filling in over the same spot: a pad's
            # ears around a keyboard's edges read as two things arriving.
            if not (progress > 0 and joining):
                screen.blit(icon, (x - PAD_RADIUS, middle - icon.get_height() // 2))
            # The hold reveals the coloured pad exactly over this red one.
            joining_at = x - PAD_RADIUS + icon.get_width() // 2
            x += icon.get_width()
        else:
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
        screen.blit(label, (x + 8, middle - label.get_height() // 2))
    for player, seat in occupied:
        colour = colour_for(player)
        # aacircle, not circle: a hard-edged disc at this size is visibly
        # stepped, and a row of them across the top of every screen is the
        # first thing anybody notices about the strip.
        pygame.draw.aacircle(screen, colour, (x, middle), PAD_RADIUS)

        number = small.render(str(player), True, (20, 20, 24))
        screen.blit(number, number.get_rect(center=(x, middle)))

        # The controller itself, drawn rather than named: "Xbox 360 Controller"
        # across the top of a game library is a string nobody reads, and the
        # shape of the pad in a player's hands is the thing they can check
        # against what they are holding. An unrecognised pad is the generic
        # drawing, which still says a pad is there.
        icon = icon_surface(seat.get("model") or seat.get("name"), icon_height, colour)
        width_used = 0
        if icon is not None:
            screen.blit(icon, (x + PAD_RADIUS + 6, middle - icon.get_height() // 2))
            width_used = 6 + icon.get_width()
        else:
            written = name_for(seat)
            if written:
                label = tiny.render(written, True, LABEL)
                screen.blit(label, (x + PAD_RADIUS + 6, middle - label.get_height() // 2))
                width_used = 6 + label.get_width()
        x += PAD_RADIUS * 2 + width_used + GAP * 2
        joining_at = x

    if progress > 0:
        draw_hold(screen, (joining_at, middle), next_seat(players, slots), progress, icon_height, joining)

    word = tiny.render(status, True, LABEL_DIM)
    screen.blit(word, (width - word.get_width() - GAP, middle - word.get_height() // 2))
    return HEIGHT


def draw_reveal(screen, centre, icon_name: str | None, colour, fraction: float, height: int) -> None:
    """A controller appearing: its drawing, revealed clockwise from twelve.

    Over a silhouette of the same in the empty seat's dark red, so the sweep
    has a shape to complete rather than a wedge of colour on its own. The
    strip, the assignment screen and the launch gate all draw a hold this
    way, so a hold looks like one thing wherever it happens.

    The icon is masked by a pie slice rather than clipped by an arc: pygame
    has no clip shape but a rectangle, so the slice is drawn opaque on a
    transparent surface of the icon's size and multiplied into the icon's
    alpha, which leaves exactly the part inside it.
    """
    under = icon_surface(icon_name, height, EMPTY)
    if under is not None and fraction < 1.0:
        screen.blit(under, under.get_rect(center=centre))
    icon = icon_surface(icon_name, height, colour)
    if icon is None:
        # No artwork to reveal: a ring, rather than nothing at all.
        radius = height // 2
        box = pygame.Rect(centre[0] - radius, centre[1] - radius, 2 * radius, 2 * radius)
        top = math.pi / 2
        pygame.draw.arc(screen, colour, box, top - 2 * math.pi * min(1.0, fraction), top, 3)
        return
    width, tall = icon.get_size()
    slice_ = wedge((width / 2, tall / 2), math.hypot(width, tall) / 2 + 2, fraction)
    if len(slice_) < 3:
        return
    mask = pygame.Surface((width, tall), pygame.SRCALPHA)
    pygame.draw.polygon(mask, (255, 255, 255, 255), slice_)
    shown = icon.copy()
    shown.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    screen.blit(shown, shown.get_rect(center=centre))


def draw_hold(
    screen, centre, player: int | None, fraction: float, height: int, icon_name: str | None = None
) -> None:
    """A hold on its way, on the strip: the joining seat's colour, its icon."""
    draw_reveal(screen, centre, icon_name, colour_for(player) if player else EMPTY_RING, fraction, height)


def draw_assign(screen, font_at, view) -> None:
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

    # The way out a controller can reach. padmap holds every pad for the length
    # of a session, so this screen answers no button -- except a longer hold on
    # a pad that already has a seat, which the daemon takes as "accept" itself.
    if view.keep_hint:
        hint = font_at(22).render(view.keep_hint, True, TEXT_DIM)
        screen.blit(hint, ((width - hint.get_width()) // 2, int(height * 0.25)))

    # The seats, across the middle, in the same colours the strip uses.
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

        # The controller under the seat, drawn rather than named -- the same
        # picture the strip uses, so a pad claimed here is recognisable up
        # there. A seat nobody is in gets the generic pad in the empty colour:
        # what is missing is a controller, and that is what it looks like.
        #
        # A hold fills that pad in, clockwise from twelve, the way the strip
        # does: padmap's progress on the seat it would claim next, and its
        # confirm -- the longer hold that accepts -- as the seated pad filling
        # in again over a dark silhouette, so the two are not one thing twice.
        below = (centre[0], middle + 62)
        model = getattr(seat, "model", None) or (seat.name if seat else None)
        if seat is None and view.progress > 0 and player == view.waiting_for:
            draw_reveal(screen, below, None, colour_for(player), view.progress, 44)
            continue
        if seat is not None and view.confirm > 0:
            draw_reveal(screen, below, model, colour_for(player), view.confirm, 44)
            continue
        icon = icon_surface(model, 44, colour_for(player) if seat else EMPTY_RING)
        if icon is not None:
            screen.blit(icon, icon.get_rect(center=below))
        else:
            label = font_at(20).render(
                (seat.name if seat else "waiting")[:22], True, TEXT if seat else EMPTY_TEXT
            )
            screen.blit(label, label.get_rect(center=(centre[0], middle + 56)))

    if view.message:
        note = font_at(22).render(view.message, True, (232, 140, 140))
        screen.blit(note, ((width - note.get_width()) // 2, int(height * 0.72)))

    # Named for both, because both work and only one of them is in the room:
    # somebody holding a pad should not have to find a keyboard to keep what
    # they have just claimed.
    keys = "A or Enter keep   Y or R start again   B or Esc back" \
        if view.state == "assigning" else "A or Enter assign   B or Esc back"
    footer = font_at(20).render(keys, True, TEXT_DIM)
    screen.blit(footer, ((width - footer.get_width()) // 2, int(height * 0.86)))
