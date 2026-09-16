"""The controller check a game passes through on its way to being launched.

Two questions, asked in order, and skipped entirely when the answer is already
yes: is there a controller at all, and does it know what the buttons on *this*
console are called. Both are the sort of thing that is invisible until a game
is on screen, at which point the person holding the pad has no way to fix it —
the emulator is fullscreen, the picker is gone, and nothing has focus. So they
are asked beforehand, while there is still a screen to ask on.

padmap answers both. Seats come from a hold on the pad itself, and a mapping is
a capture padmap already knows how to run; all this decides is *whether* to ask
and *what* to send, from the events coming back.

Model only, and deliberately: what a gate looks like is the runner's business.
What is tested here is that a machine with a mapped controller is never
interrupted, and one without is interrupted exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Where the gate is, which is also what the runner draws.
CHECKING = "checking"   # connected, nothing decided yet
SEATING = "seating"     # no controller: hold a button on one
MAPPING = "mapping"     # a controller with no idea what this console's buttons are
READY = "ready"         # go and play
SKIPPED = "skipped"     # asked, declined; go and play anyway

# GOTG's platform -> the padmap layout whose controls the wizard walks. Wii is
# GameCube because Dolphin is configured here for GameCube pads only. Anything
# padmap has no layout for falls back to the generic pad rather than borrowing
# a console's: NES walked through SNES would ask for buttons that do not exist.
LAYOUTS = {
    "gamecube": "gamecube",
    "wii": "gamecube",
    "wiiu": "wiiu",
    "switch": "switch",
    "n64": "n64",
    "snes": "snes",
    "genesis": "genesis",
}
FALLBACK_LAYOUT = "generic"


def layout_for(platform: str) -> str:
    """The layout id to capture against, never None.

    A platform nobody has listed is still playable: the generic pad is the
    control set every one of these consoles is a subset of, so the capture is
    shorter than it should be rather than absent.
    """
    return LAYOUTS.get((platform or "").lower(), FALLBACK_LAYOUT)


def console_scope(layout: str) -> str:
    """padmap's scope string for "every game on this console"."""
    return f"console:{layout}"


@dataclass(frozen=True)
class Seat:
    """A seated player, and whether this console means anything to it."""

    player: int
    name: str = ""
    mappings: tuple[str, ...] = ()

    def mapped(self, scope: str) -> bool:
        return scope in self.mappings


@dataclass(frozen=True)
class Gate:
    """What the check knows, rebuilt from each event rather than mutated."""

    platform: str = ""
    state: str = CHECKING
    seats: tuple[Seat, ...] = ()
    # The wizard, while one is running.
    control: str = ""
    label: str = ""
    index: int = 0
    total: int = 0
    conflict: str = ""
    message: str = ""
    # Set once the gate has asked for something, so that a state event
    # arriving mid-flow cannot send the same command twice.
    asked: bool = False

    @property
    def layout(self) -> str:
        return layout_for(self.platform)

    @property
    def scope(self) -> str:
        return console_scope(self.layout)

    @property
    def seated(self) -> int:
        return len(self.seats)

    @property
    def unmapped(self) -> Seat | None:
        """The first seated pad this console means nothing to, or None.

        First rather than all: the wizard runs one pad at a time, and a second
        unmapped pad is the same question asked again after this one closes.
        """
        for seat in self.seats:
            if not seat.mapped(self.scope):
                return seat
        return None

    @property
    def done(self) -> bool:
        return self.state in (READY, SKIPPED)

    @property
    def prompt(self) -> str:
        """What to say to whoever is in front of the screen."""
        if self.state == SEATING:
            return "hold a button on the controller you want to play with"
        if self.state == MAPPING:
            if self.conflict:
                return f"that one is already {self.conflict} — try another"
            return f"press {self.label or self.control}"
        if self.state == READY:
            return "starting the game"
        return "checking controllers"


def seats_from(players: list | None) -> tuple[Seat, ...]:
    """The seated pads a state event reports, in player order."""
    found = [
        Seat(
            player=p["player"],
            name=str(p.get("name") or ""),
            mappings=tuple(str(m) for m in (p.get("mappings") or [])),
        )
        for p in (players or [])
        if isinstance(p, dict) and isinstance(p.get("player"), int)
    ]
    return tuple(sorted(found, key=lambda s: s.player))


def decide(gate: Gate) -> tuple[Gate, dict | None]:
    """Where a gate goes next, and the one command that gets it there.

    Called after every event rather than only at the start, because both
    answers can change underneath it: a controller switched on fills a seat,
    and a wizard finishing fills in a mapping.
    """
    if gate.done or gate.asked:
        return gate, None
    if gate.seated == 0:
        # A session rather than a quiet listen. This is the one moment where
        # grabbing every pad costs nothing -- no game is running yet, and the
        # screen in front of the person is this one.
        return replace(gate, state=SEATING, asked=True), {"cmd": "begin", "players": 4}
    seat = gate.unmapped
    if seat is not None:
        return replace(gate, state=MAPPING, asked=True), {
            "cmd": "map",
            "player": seat.player,
            "layout": gate.layout,
            "scope": gate.scope,
        }
    return replace(gate, state=READY), None


def apply(gate: Gate, event: dict) -> Gate:
    """One padmap event, folded in. Anything unknown leaves it unchanged."""
    kind = event.get("event")

    if kind == "state":
        return replace(gate, seats=seats_from(event.get("players")))

    if kind == "claim":
        # A seat taken during the gate's own session. Accepting is what turns
        # it into a real assignment, and the runner sends that; here it is
        # only the news that there is now somebody to play with.
        player = event.get("player")
        if not isinstance(player, int):
            return gate
        others = tuple(s for s in gate.seats if s.player != player)
        seat = Seat(player=player, name=str(event.get("name") or ""))
        return replace(
            gate,
            seats=tuple(sorted((*others, seat), key=lambda s: s.player)),
            message="",
        )

    if kind == "mapping":
        if event.get("done"):
            # Whether it was stored or abandoned, the wizard is closed and the
            # gate has to look again: a stored capture fills the mapping in,
            # and an abandoned one leaves the pad exactly as unmapped as it
            # was -- which is a decision, not a reason to ask twice.
            stored = bool(event.get("stored"))
            return replace(
                gate,
                state=CHECKING if stored else SKIPPED,
                asked=False,
                control="",
                label="",
                conflict="",
            )
        return replace(
            gate,
            state=MAPPING,
            control=str(event.get("control") or ""),
            label=str(event.get("label") or ""),
            index=int(event.get("index") or 0),
            total=int(event.get("total") or 0),
            conflict=str(event.get("conflict") or ""),
        )

    if kind == "accepted":
        # The seats are real now. Ask again rather than assuming: what padmap
        # writes into `mappings` is the answer to the second question.
        return replace(gate, asked=False)

    if kind == "error":
        return replace(gate, message=str(event.get("message") or "padmap said no"))

    return gate
