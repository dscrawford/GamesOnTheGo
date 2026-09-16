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


# The padmap layout -> the drawing that stands for it. Only what exists: a
# console with no artwork gets the generic pad, which still shows a stick, a
# d-pad and four face buttons in the right places.
ARTWORK = {
    "gamecube": "gamecube",
    "n64": "n64",
    "snes": "snes",
    "genesis": "megadrive",
}
FALLBACK_ARTWORK = "generic"

# padmap names controls the way SDL does; the diagrams that predate padmap name
# their anchors the way ares does. Tried in that order, so gamecube -- drawn for
# this screen -- needs no translation, and snes and n64 still light up.
ANCHOR_ALIASES = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "start": "Start", "back": "Select", "guide": "Home",
    "dpup": "Up", "dpdown": "Down", "dpleft": "Left", "dpright": "Right",
    "leftshoulder": "L", "rightshoulder": "R",
    "lefttrigger": "L2", "righttrigger": "Z",
}


# What each control is called on a GameCube pad, mirroring padmap's gamecube
# layout. Here because Dolphin publishes no ares console, so the binding screen
# has no table of its own to read and would otherwise say a GameCube pad has no
# buttons. Held honest by a test that checks it against padmap's own control
# set, which is the same test that checks the artwork.
GAMECUBE_CONTROLS = {
    "a": "A",
    "b": "B",
    "x": "X",
    "y": "Y",
    "start": "Start",
    "dpup": "D-pad up",
    "dpdown": "D-pad down",
    "dpleft": "D-pad left",
    "dpright": "D-pad right",
    "leftshoulder": "L",
    "rightshoulder": "R",
    "righttrigger": "Z",
    "rightstick_up": "C-stick up",
    "rightstick_down": "C-stick down",
    "rightstick_left": "C-stick left",
    "rightstick_right": "C-stick right",
}

CONTROLS = {"gamecube": GAMECUBE_CONTROLS}


def controls_for(layout: str) -> dict[str, str]:
    """Every control this console has, and what it is called. Empty when
    nothing here knows -- which is a screen that says so, not a guess."""
    return CONTROLS.get(layout, {})


def artwork_for(layout: str) -> str:
    return ARTWORK.get(layout, FALLBACK_ARTWORK)


def anchor_names(control: str) -> tuple[str, ...]:
    """Which anchors could mark this control, best first."""
    if not control:
        return ()
    alias = ANCHOR_ALIASES.get(control)
    return (control, alias) if alias else (control,)


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
    # padmap has a session open. Everything about binding buttons happens
    # inside one: `map` is refused with "mapping needs an open session", and
    # `accept` is what closes it -- so accepting before mapping, which is the
    # obvious order, is the one order that cannot work.
    session: bool = False
    # The command sent and not yet answered. One at a time, so a state event
    # arriving mid-flow cannot send the same command twice.
    awaiting: str = ""
    # Commands padmap has refused. Not retried, because the same command would
    # be refused the same way for ever.
    refused: tuple[str, ...] = ()
    # A capture is running. Every step of one is a `mapping` event, and a step
    # is not an answer to `map` -- treating it as one sends a second `map`,
    # which starts a second capture, which sends more steps.
    wizard: bool = False

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

    The order is the whole of this function. Binding buttons needs a session
    open, and accepting closes it, so: begin, seat somebody, bind, and only
    then accept.
    """
    if gate.done or gate.awaiting or gate.wizard:
        return gate, None

    wanted = gate.seated == 0 or gate.unmapped is not None
    if not wanted:
        if gate.session:
            # Nothing left to ask. Accepting is what turns the claims into
            # assignments, republishes the pads and writes the emulator's
            # configuration -- so the game starts with them.
            return replace(gate, awaiting="accept"), {"cmd": "accept"}
        return replace(gate, state=READY), None

    if not gate.session:
        if "begin" in gate.refused:
            return gate, None
        # A session rather than a quiet listen. This is the one moment where
        # grabbing every pad costs nothing -- no game is running yet, and the
        # screen in front of the person is this one.
        return replace(
            gate,
            state=SEATING if gate.seated == 0 else MAPPING,
            awaiting="begin",
        ), {"cmd": "begin", "players": 4}

    if gate.seated == 0:
        # Waiting on a hold. Nothing to send: padmap is reading the pads.
        return replace(gate, state=SEATING), None

    seat = gate.unmapped
    if seat is None or "map" in gate.refused:
        return gate, None
    return replace(gate, state=MAPPING, awaiting="map"), {
        "cmd": "map",
        "player": seat.player,
        "layout": gate.layout,
        "scope": gate.scope,
    }


def apply(gate: Gate, event: dict) -> Gate:
    """One padmap event, folded in. Anything unknown leaves it unchanged."""
    kind = event.get("event")

    if kind == "state":
        # "assigning" is padmap saying a session is open, which is the answer
        # to `begin` -- there is no other acknowledgement of it.
        session = event.get("state") == "assigning"
        return replace(
            gate,
            seats=seats_from(event.get("players")),
            session=session,
            awaiting="" if gate.awaiting == "begin" and session else gate.awaiting,
        )

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
            # A stored capture fills the mapping in and the gate looks again --
            # which finds nothing left to ask and accepts. An abandoned one is
            # a decision, not a reason to ask twice.
            stored = bool(event.get("stored"))
            return replace(
                gate,
                state=CHECKING if stored else SKIPPED,
                awaiting="",
                wizard=False,
                control="",
                label="",
                conflict="",
            )
        return replace(
            gate,
            state=MAPPING,
            awaiting="",
            wizard=True,
            control=str(event.get("control") or ""),
            label=str(event.get("label") or ""),
            index=int(event.get("index") or 0),
            total=int(event.get("total") or 0),
            conflict=str(event.get("conflict") or ""),
        )

    if kind == "accepted":
        # Seats, republished pads and emulator configuration, all written. The
        # only thing left was the game.
        return replace(gate, state=READY, session=False, awaiting="")

    if kind == "error":
        # Whatever was in flight is not coming. Remembered as refused so the
        # same command is not sent again on the next frame, for ever.
        refused = gate.refused
        if gate.awaiting and gate.awaiting not in refused:
            refused = (*refused, gate.awaiting)
        return replace(
            gate,
            message=str(event.get("message") or "padmap said no"),
            awaiting="",
            refused=refused,
            # A refusal to bind is the end of the asking; a refusal to open a
            # session leaves the screen up, because plugging a controller in
            # makes padmap open one by itself.
            state=SKIPPED if gate.awaiting in ("map", "accept") else gate.state,
        )

    return gate
