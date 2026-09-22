"""Who is holding a controller, across the top of the screen.

Only the seats somebody is in. A seat nobody is in is not information on a
screen that is mostly a game library: three red rings saying "still nobody"
above the games is a permanent complaint about a machine that is working
exactly as it should with one controller. When *nothing* is connected that is
worth saying, and it is said once, as an X.

The assignment screen is the other way round and deliberately: there, the free
seats are the whole point, because filling them is what the screen is for.

A colour per player rather than a number alone. The number is there, but the
colour is what makes "am I player 2" answerable from the sofa, and it is the
same four colours everywhere a player is named -- so a seat, a name tag in a
co-op game and a slot in the split-screen frame agree without anybody matching
words.
"""

from __future__ import annotations

from . import config

# Player 1-4. Nintendo's four-player order, which is what the consoles this
# runs use on their own port lights and what a Switch shows above a head.
PLAYER_COLOURS = [
    tuple(int(part) for part in entry)
    for entry in config.get("theme.players", []) or []
    if isinstance(entry, (list, tuple)) and len(entry) == 3
] or [(96, 176, 255), (240, 96, 96), (120, 216, 120), (248, 208, 88)]

# An empty seat is red rather than grey. Grey reads as decoration; red reads as
# something to do -- which it is, since a seat nobody is in is a controller
# nobody can play with.
EMPTY = config.colour("theme.colours.empty", (54, 30, 32))
EMPTY_RING = config.colour("theme.colours.empty_ring", (208, 78, 78))
EMPTY_TEXT = config.colour("theme.colours.empty_text", (208, 78, 78))
LABEL = config.colour("theme.colours.text", (232, 232, 236))
LABEL_DIM = config.colour("theme.colours.text_dim", (150, 150, 158))
PANEL = config.colour("theme.colours.panel", (26, 26, 30))
READY = config.colour("theme.colours.ready", (108, 196, 116))

# The strip's own height. The caller offsets everything below by it, so the
# screens underneath carry no copy of the number.
HEIGHT = int(config.get("theme.strip.height", 54))
PAD_RADIUS = int(config.get("theme.strip.pad_radius", 14))
GAP = int(config.get("theme.strip.gap", 10))


def colour_for(player: int) -> tuple[int, int, int]:
    """The colour of a seat. Players past the fourth wrap rather than fail:
    padmap will seat as many as are asked for, and a fifth pad with no colour
    is worse than a fifth pad sharing one."""
    return PLAYER_COLOURS[(player - 1) % len(PLAYER_COLOURS)]


def seats(players: list[dict], slots: int = 4) -> list[tuple[int, dict]]:
    """The occupied seats, in player order, as (player number, pad).

    The number is carried rather than implied by position: players 1 and 3
    draw as 1 and 3, in their own colours, with nothing between them. Sliding 3
    into the second place would tell somebody they are player two when every
    emulator on the machine thinks otherwise.

    `slots` is accepted and ignored. It is what padmap was asked for, which
    stopped mattering when the empty seats stopped being drawn -- kept in the
    signature so the caller does not have to know that.
    """
    found = [
        (p["player"], p)
        for p in players
        if isinstance(p, dict) and isinstance(p.get("player"), int)
    ]
    return sorted(found, key=lambda pair: pair[0])


def name_for(seat: dict | None) -> str:
    """What to write beside the badge, or nothing at all.

    An empty seat says nothing: the red ring already says it, and "empty"
    beside it is the same fact twice in a row of four -- which reads as three
    words of noise across the top of every screen.

    padmap names its clones "padmap Player N", which is true and says nothing
    either: beside a badge that already says 2 in player two's colour, the
    useful half is the controller it stands for.
    """
    if seat is None:
        return ""
    name = str(seat.get("name") or "").strip()
    if not name:
        return "pad"
    prefix = "padmap Player "
    if name.startswith(prefix) and name[len(prefix):].strip().isdigit():
        return str(seat.get("model") or "pad")
    return name


def strip_status(status: str, seated: int) -> str:
    """What the right-hand end of the strip says.

    "padmap ready" is true and useless when no seat is taken: the question in
    front of somebody then is not what padmap is doing, it is how to make it do
    anything. And the answer is the hold padmap is already listening for while
    the picker is up -- not the key that opens the assignment screen, which is
    the long way round and is on a keyboard nobody took to the sofa.
    """
    if seated == 0 and status in ("idle", "ready"):
        # Both ways in, because the keyboard is no longer the way that always
        # worked: it takes a seat like everything else now (keys.py), and
        # space is how it asks.
        return "hold a button on a controller, or space"
    return status_text(status)


def status_text(status: str) -> str:
    """padmap's state, in words that mean something to whoever is reading it
    rather than the daemon's own vocabulary."""
    return {
        "offline": "padmap not running",
        "idle": "padmap ready",
        "assigning": "hold a button on each controller",
        "ready": "controllers assigned",
    }.get(status, status)


def next_seat(players: list[dict], slots: int = 4) -> int | None:
    """Which player a hold would claim next, or None when the seats are full.

    The lowest free number rather than the one after the last: a player two
    who unplugged leaves a gap, and the next person to pick a pad up is two
    again -- which is what padmap seats them as, and what the ring above the
    grid has to agree with.
    """
    taken = {player for player, _ in seats(players, slots)}
    for player in range(1, slots + 1):
        if player not in taken:
            return player
    return None


def wedge(centre: tuple[float, float], radius: float, fraction: float, step: float = 5.0) -> list[tuple[float, float]]:
    """A pie slice from twelve o'clock, clockwise, `fraction` of the way round.

    The polygon that reveals a controller while its button is held: what is
    drawn is the icon inside this shape, so the pad appears the way a clock
    hand would uncover it. Twelve rather than three because that is where a
    person starts reading a circle, and clockwise because that is the way
    time goes; pygame's own arcs run the other way from the other place,
    which is why this is a polygon and not one of those.

    Screen coordinates, y down. Empty when there is nothing to show, and the
    whole disc for anything past one -- a fraction is a fraction.
    """
    if fraction <= 0:
        return []
    fraction = min(1.0, fraction)
    cx, cy = centre
    points = [(cx, cy)]
    sweep = 360.0 * fraction
    angle = 0.0
    while angle < sweep:
        points.append(_on_circle(cx, cy, radius, angle))
        angle += step
    points.append(_on_circle(cx, cy, radius, sweep))
    return points


def _on_circle(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    """Clockwise from twelve, in a coordinate system where y grows downward."""
    import math

    theta = math.radians(degrees)
    return (cx + radius * math.sin(theta), cy - radius * math.cos(theta))
