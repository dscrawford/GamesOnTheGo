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

No session. It used to open one -- `begin`, which grabs every pad -- and a
session's pad list is fixed the moment it opens: a controller switched on
while the gate was up could not take a seat, and the second player in the
room was told to wait for a launch that was already waiting for them.
padmap's seating mode rescans as it goes, grabs nothing, and seats a held
pad the same way; `map` no longer needs a session either. So: unseat, listen,
hold, map, and the door in seat.py for the second that starts the game.

Model only, and deliberately: what a gate looks like is the runner's business.
What is tested here is the order -- unseat, seat, map -- and that a mapped
pad is asked for its hold and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import schemes

# Where the gate is, which is also what the runner draws.
CHECKING = "checking"   # connected, nothing decided yet
SEATING = "seating"     # no controller: hold a button on one
MAPPING = "mapping"     # a controller with no idea what this console's buttons are
READY = "ready"         # seated and mapped; the runner's door decides the rest
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
    # padmap has a session open -- somebody else's `begin`. Noted, not used:
    # this gate opens none, and a claim arrives the same way either way.
    session: bool = False
    # Seating has been asked for. padmap does not acknowledge it, so it is
    # sent once and believed; a refusal names the command and is remembered.
    listening: bool = False
    # The command sent and not yet answered. One at a time, so a state event
    # arriving mid-flow cannot send the same command twice.
    awaiting: str = ""
    # Commands padmap has refused. Not retried, because the same command would
    # be refused the same way for ever.
    refused: tuple[str, ...] = ()
    # What the wizard has bound so far, control -> binding, from its last
    # step. The screen says what the previous press became, which is the
    # only way to see a binding while making it.
    captured: dict = field(default_factory=dict)
    # A capture is running. Every step of one is a `mapping` event, and a step
    # is not an answer to `map` -- treating it as one sends a second `map`,
    # which starts a second capture, which sends more steps.
    wizard: bool = False
    # The daemon has been told to forget its seats, once. Once, because the
    # seats that come after are this launch's own, taken by a hold in front
    # of this screen, and forgetting those would be a gate nobody gets past.
    unseated: bool = False
    # A hold on a pad that has no seat yet, as far round as it has got:
    # padmap's `progress`, which is the seating screen's whole answer to "is
    # it registering my button?"
    progress: float = 0.0
    # padmap's `confirm`, when a session somebody else opened is being
    # accepted by a hold. Drawn if it comes; nothing here waits for it.
    confirm: float = 0.0

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
    if (
        gate.seated and not gate.unseated and not gate.listening
        and not gate.session and "unseat" not in gate.refused
    ):
        return replace(gate, state=SEATING, awaiting="unseat", unseated=True), {"cmd": "unseat"}

    # Then listen. Seating rather than a session: it grabs nothing, it seats
    # a held pad exactly as a session would, and it goes on looking for pads,
    # so one switched on while this screen is up can take the next seat.
    # From here every seat is this launch's own, so the forgetting is done.
    if not gate.listening and "seating" not in gate.refused:
        return replace(
            gate, state=SEATING if gate.seated == 0 else gate.state, listening=True, unseated=True
        ), {
            "cmd": "seating",
            "open": True,
            "players": 4,
        }

    if gate.seated == 0:
        # Waiting on a hold. Nothing to send: padmap is reading the pads.
        return replace(gate, state=SEATING), None

    seat = gate.unmapped
    if seat is None or "map" in gate.refused:
        # Seated and mapped. Nothing starts here: the runner's door waits for
        # a fresh hold of a full second on the clone padmap has published.
        return replace(gate, state=READY), None
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
        # "assigning" is padmap saying a session is open -- not this gate's,
        # which opens none, but a claim arrives the same way in either.
        session = event.get("state") == "assigning"
        seats = seats_from(event.get("players"))
        # `begin` is answered by the state that says "assigning"; `unseat` by
        # the one with nobody in it. Not by the next state whatever it says:
        # the daemon greets a connection with one and answers `status` with
        # another, and the second was still in the socket when the gate sent
        # unseat -- so it was read as the answer, with everybody still seated,
        # and the gate went on to map a pad it had meant to forget.
        answered = gate.awaiting == "unseat" and not seats
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
            progress=0.0,
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
        captured = event.get("captured")
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
            captured=dict(captured) if isinstance(captured, dict) else gate.captured,
        )

    if kind == "progress":
        frac = event.get("frac")
        return replace(gate, progress=float(frac) if isinstance(frac, (int, float)) else 0.0)

    if kind == "confirm":
        frac = event.get("frac")
        return replace(gate, confirm=float(frac) if isinstance(frac, (int, float)) else 0.0)

    if kind == "accepted":
        # Somebody else's session closed. The seats it made are in the next
        # state event; nothing here was waiting on it.
        return replace(gate, session=False, confirm=0.0)

    if kind == "error":
        # Whatever was in flight is not coming. Remembered as refused so the
        # same command is not sent again on the next frame, for ever.
        refused = gate.refused
        if gate.awaiting and gate.awaiting not in refused:
            refused = (*refused, gate.awaiting)
        # seating is sent without an awaiting, since padmap never answers it
        # -- except to refuse it by name, which is the one answer it gives.
        if str(event.get("message") or "") == 'unknown command "seating"' and "seating" not in refused:
            refused = (*refused, "seating")
        return replace(
            gate,
            message=str(event.get("message") or "padmap said no"),
            awaiting="",
            refused=refused,
            # A refusal to bind is the end of the asking. A refusal to unseat
            # is not: the seats stand, and the gate looks at them as it
            # always did.
            state=SKIPPED if gate.awaiting == "map" else
                  CHECKING if gate.awaiting == "unseat" else gate.state,
        )

    return gate


def rebind(gate: Gate) -> tuple[Gate, dict | None]:
    """Walk the buttons again, on purpose, for the first seated pad.

    Asked for from the door: somebody looked at what their buttons do and
    wants them otherwise. Back through the wizard, then the door again.
    """
    if not gate.seats or gate.wizard or gate.awaiting:
        return gate, None
    seat = gate.seats[0]
    return replace(gate, state=MAPPING, awaiting="map"), {
        "cmd": "map",
        "player": seat.player,
        "layout": gate.layout,
        "scope": gate.scope,
    }


def without_controllers(reason: str, seconds_left: float) -> tuple[str, str]:
    """What the launch window says when padmap cannot be asked: why, and how long.

    Here rather than in seat.py because seat.py imports pygame, and these are
    words. The reason is padmap's own sentence, so "padmap is not installed"
    and "padmap would not start: no permission for uinput" arrive as they are.
    """
    left = max(0, int(seconds_left + 0.999))
    return (
        reason or "padmap is not running",
        f"starting without controllers in {left} s — Enter starts now, Esc too",
    )


# A press this soon after the door opens is the old hold, whatever the pad's
# state said. Two ways it arrives late: SDL reports a button already down
# within a frame of opening, and padmap forwards the state it held back
# during the wizard when the wizard ends -- measured at 120 ms after the
# capture closed, which is after the door has opened. A full second covers
# both with room; a person who presses within a second of the screen
# appearing lets go and holds again, which is what the footer says to do.
ARM_QUIET = 1.0


@dataclass
class GoHold:
    """The second that starts the game, counted only from a fresh press.

    One hold ran through everything: it finished the wizard, it was padmap's
    confirm, and it was still down when the clone appeared -- padmap
    forwards a Steam Controller's button *state*, so the clone showed A
    pressed from its first frame, and the door counted it. The door now
    asks the pads what is down when it opens; if anything is, it is armed
    by nothing but a release. If nothing is, a press after a quarter
    second's quiet is a fresh one. The clock is passed in.
    """

    seconds: float = 1.0
    opened: float = 0.0
    held_at_open: bool = False
    armed: bool = False
    since: float | None = None

    def pressed(self, now: float) -> None:
        if not self.armed:
            if not self.held_at_open and now - self.opened >= ARM_QUIET:
                self.armed = True
            else:
                return
        if self.since is None:
            self.since = now

    def released(self, now: float) -> None:
        self.armed = True
        self.since = None

    def progress(self, now: float) -> float:
        if self.since is None:
            return 0.0
        elapsed = now - self.since
        return 1.0 if elapsed >= self.seconds - 1e-3 else max(0.0, elapsed / self.seconds)

    def done(self, now: float) -> bool:
        return self.progress(now) >= 1.0
