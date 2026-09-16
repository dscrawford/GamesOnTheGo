"""Who is holding a controller, across the top of the screen.

Four seats, always drawn, because the empty ones are the useful part: a person
whose pad did not come back after a replug is looking for the seat that went
grey, and a strip that only showed what was connected would answer that
question by having one fewer thing on it than they remember.

A colour per player rather than a number alone. The number is there, but the
colour is what makes "am I player 2" answerable from the sofa, and it is the
same four colours everywhere a player is named -- so a seat, a name tag in a
co-op game and a slot in the split-screen frame agree without anybody matching
words.
"""

from __future__ import annotations

# Player 1-4. Nintendo's four-player order, which is what the consoles this
# runs use on their own port lights and what a Switch shows above a head.
PLAYER_COLOURS = [
    (96, 176, 255),   # 1 blue
    (240, 96, 96),    # 2 red
    (120, 216, 120),  # 3 green
    (248, 208, 88),   # 4 yellow
]

# An empty seat is red rather than grey. Grey reads as decoration; red reads as
# something to do -- which it is, since a seat nobody is in is a controller
# nobody can play with.
EMPTY = (54, 30, 32)
EMPTY_RING = (208, 78, 78)
EMPTY_TEXT = (208, 78, 78)
LABEL = (232, 232, 236)
LABEL_DIM = (150, 150, 158)
PANEL = (26, 26, 30)

# The strip's own height. The caller offsets everything below by it, so the
# screens underneath carry no copy of the number.
HEIGHT = 54
PAD_RADIUS = 14
GAP = 10


def colour_for(player: int) -> tuple[int, int, int]:
    """The colour of a seat. Players past the fourth wrap rather than fail:
    padmap will seat as many as are asked for, and a fifth pad with no colour
    is worse than a fifth pad sharing one."""
    return PLAYER_COLOURS[(player - 1) % len(PLAYER_COLOURS)]


def seats(players: list[dict], slots: int) -> list[dict | None]:
    """One entry per seat, in order, `None` where nobody is sitting.

    Built from the seat number rather than from position in the list, so a
    session holding players 1 and 3 draws a gap at 2 instead of sliding 3 left
    into a seat it does not have.
    """
    by_player = {
        p["player"]: p
        for p in players
        if isinstance(p, dict) and isinstance(p.get("player"), int)
    }
    highest = max([*by_player, slots], default=slots)
    return [by_player.get(seat) for seat in range(1, max(slots, highest) + 1)]


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
    anything. So the idle-and-empty case is the only one that names a key.
    """
    if status == "idle" and seated == 0:
        return "press C to assign controllers"
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
