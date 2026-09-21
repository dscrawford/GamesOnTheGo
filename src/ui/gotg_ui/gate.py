"""The controller check a game passes through on its way to being launched.

Every game begins the same way: nobody is seated, and the person about to
play picks a controller up and holds a button. A seat is something taken in
front of the screen about to be used, not something the machine remembers
you having -- so the first thing the gate does with a daemon that remembers
is tell it to forget. Then the question that was always here: does this pad
know what the buttons on *this* console are called. That one is invisible
until a game is on screen, at which point the person holding the pad has no
way to fix it -- the emulator is fullscreen, the picker is gone, and nothing
has focus. So it is asked beforehand, while there is still a screen to ask on.

padmap answers all of it. Seats come from a hold on the pad itself, and a
mapping is a capture padmap already knows how to run; all this decides is
*what* to send, and when, from the events coming back.

Model only, and deliberately: what a gate looks like is the runner's business.
What is tested here is the order -- unseat, seat, map, accept -- and that a
mapped pad is asked for its hold and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import schemes

# Where the gate is, which is also what the runner draws.
CHECKING = "checking"   # connected, nothing decided yet
SEATING = "seating"     # no controller: hold a button on one
MAPPING = "mapping"     # a controller with no idea what this console's buttons are
READY = "ready"         # go and play
SKIPPED = "skipped"     # asked, declined; go and play anyway

def scheme_for(platform: str) -> schemes.Scheme:
    """The controller a platform is played with, from `config/controllers/`."""
    return schemes.for_platform(platform)


def layout_for(platform: str) -> str:
    """The padmap layout to capture against, never None.

    Keyed by platform rather than by layout, because several controllers share
    one: an NES pad, a Game Boy and a GBA are all captured against the generic
    layout and are three different drawings.
    """
    return scheme_for(platform).layout


def artwork_for(platform: str) -> str:
    return scheme_for(platform).artwork


def controls_for(platform: str) -> dict[str, str]:
    """Every control, and what the button says. Empty when no file describes
    this platform -- which is a screen that says so, not a guess."""
    return dict(scheme_for(platform).controls)


def anchor_names(control: str, platform: str = "") -> tuple[str, ...]:
    """Which anchors could mark this control, best first."""
    return scheme_for(platform).anchor_names(control)


def console_scope(layout: str) -> str:
    """padmap's scope string for "every game on this console"."""
    return f"console:{layout}"


@dataclass(frozen=True)
class Seat:
    """A seated player, and whether it knows where its own buttons are."""

    player: int
    name: str = ""
    mappings: tuple[str, ...] = ()
    # padmap's own word for it: mapped, not merely known. True for a pad it
    # bound from the kernel's BTN_ codes as well as one somebody captured by
    # hand, which is the whole point -- a standard controller arrives working.
    configured: bool = False

    def mapped(self, scope: str) -> bool:
        """Whether this pad can play this console.

        Any capture, not a capture for this scope. padmap falls back to the
        universal mapping when a console has none of its own, so a pad bound
        once is bound for everything -- and a gate that demanded
        `console:<this one>` asked again on every new platform for a pad that
        already worked.
        """
        return self.configured or bool(self.mappings) or scope in self.mappings


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
    # The daemon has been told to forget its seats, once. Once, because the
    # seats that come after are this launch's own, taken by a hold in front
    # of this screen, and forgetting those would be a gate nobody gets past.
    unseated: bool = False

    @property
    def scheme(self) -> schemes.Scheme:
        """The controller this platform is played with."""
        return scheme_for(self.platform)

    @property
    def layout(self) -> str:
        return self.scheme.layout

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
            configured=bool(p.get("configured", False)),
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

    # First, and once: whatever the daemon remembers is not this launch's.
    # Refused -- an older daemon, or a session somebody else has open -- means
    # the seats stand and the gate goes on as it always did.
    if gate.seated and not gate.unseated and not gate.session and "unseat" not in gate.refused:
        return replace(gate, state=SEATING, awaiting="unseat", unseated=True), {"cmd": "unseat"}

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
        seats = seats_from(event.get("players"))
        # `begin` is answered by the state that says "assigning"; `unseat` by
        # the one with nobody in it. Not by the next state whatever it says:
        # the daemon greets a connection with one and answers `status` with
        # another, and the second was still in the socket when the gate sent
        # unseat -- so it was read as the answer, with everybody still seated,
        # and the gate went on to map a pad it had meant to forget.
        answered = (gate.awaiting == "begin" and session) or (gate.awaiting == "unseat" and not seats)
        return replace(
            gate,
            seats=seats,
            session=session,
            awaiting="" if answered else gate.awaiting,
        )

    if kind == "claim":
        # A seat taken during the gate's own session. Accepting is what turns
        # it into a real assignment, and the runner sends that; here it is
        # only the news that there is now somebody to play with. A claim
        # while an unseat is unanswered is somebody holding a button in front
        # of this screen, which is this launch's own seat: the forgetting is
        # done, whatever the daemon has said so far.
        player = event.get("player")
        if not isinstance(player, int):
            return gate
        others = tuple(s for s in gate.seats if s.player != player)
        seat = Seat(player=player, name=str(event.get("name") or ""))
        return replace(
            gate,
            seats=tuple(sorted((*others, seat), key=lambda s: s.player)),
            message="",
            awaiting="" if gate.awaiting == "unseat" else gate.awaiting,
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
            # makes padmap open one by itself. A refusal to unseat is neither:
            # the seats stand, and the gate looks at them as it always did.
            state=SKIPPED if gate.awaiting in ("map", "accept") else
                  CHECKING if gate.awaiting == "unseat" else gate.state,
        )

    return gate
