"""Taking a seat: hold a button on each controller, in the order you want.

Four identical adapter ports report the same name, phys, uniq, vendor, product
and version, and differ only by an ordinal the kernel hands out in plug order.
No file can pin player one to hardware that is genuinely indistinguishable, so
padmap asks the person holding them — and this is the screen that asks.

The whole flow is driven by what comes back from the daemon, not by the pad.
padmap holds EVIOCGRAB for the length of a session, so while this screen is up
no controller input reaches this program at all: a button held here arrives as
a `claim` event and never as a pygame one. That is also why the way out is the
keyboard, or the one gamepad button padmap has not grabbed — there isn't one,
so it is the keyboard.

Model only. What it looks like is drawing's business; what is tested is what a
sequence of events leaves on screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import trace


@dataclass(frozen=True)
class Seat:
    """One player, as far as the assignment knows."""

    player: int
    name: str = ""
    icon: str = ""
    configured: bool = True


@dataclass(frozen=True)
class Assignment:
    """What the screen shows, rebuilt from each event rather than mutated.

    Immutable so a frame that draws while an event is being applied cannot see
    half of it — the picker draws from whatever the last complete state was.
    """

    state: str = "idle"
    slots: int = 4
    pads: int = 0
    seats: tuple[Seat, ...] = ()
    progress: float = 0.0
    confirm: float = 0.0
    message: str = ""
    finished: bool = False

    @property
    def seated(self) -> int:
        return len(self.seats)

    @property
    def waiting_for(self) -> int | None:
        """The seat a held button would claim next, or None when full."""
        taken = {seat.player for seat in self.seats}
        for player in range(1, self.slots + 1):
            if player not in taken:
                return player
        return None

    @property
    def keep_hint(self) -> str:
        """The way off this screen that a controller can reach, once there is
        one to reach it with.

        padmap grabs every pad for the length of a session, so nothing on this
        screen answers a button -- except a longer hold on a pad that already
        has a seat, which the daemon itself takes as "accept". That was true
        before this line existed and nothing said so, which left somebody
        holding a controller looking at a screen that only a keyboard could
        close.
        """
        if self.state != "assigning" or not self.seats:
            return ""
        return "or hold a button on a seated controller to start"

    @property
    def prompt(self) -> str:
        """What to tell the person in front of the screen, right now."""
        if self.finished:
            return "controllers assigned"
        if self.state != "assigning":
            return "press A, or Enter, to assign controllers"
        nxt = self.waiting_for
        if nxt is None:
            return "every seat is taken — press Enter to keep it"
        if self.pads == 0:
            return "no controllers found — plug one in"
        return f"hold a button on the controller for player {nxt}"


def apply(assignment: Assignment, event: dict) -> Assignment:
    """One event, folded in. Anything unknown leaves it exactly as it was.

    A front-end that rejected events it did not know would break every time
    the daemon grew one, and the daemon is the authority here -- so the rule
    is to render what is understood and ignore the rest.
    """
    kind = event.get("event")

    if kind == "state":
        state = event.get("state", assignment.state)
        slots = event.get("slots")
        seats = tuple(
            Seat(
                player=p["player"],
                name=str(p.get("name") or ""),
                icon=str(p.get("icon") or ""),
            )
            for p in (event.get("players") or [])
            if isinstance(p, dict) and isinstance(p.get("player"), int)
        )
        return replace(
            assignment,
            state=state if isinstance(state, str) else assignment.state,
            slots=slots if isinstance(slots, int) and slots > 0 else assignment.slots,
            seats=tuple(sorted(seats, key=lambda s: s.player)),
            # A session that ends returns to idle or ready with the claims in
            # `players`; the hold in flight is not part of that.
            progress=0.0 if state != "assigning" else assignment.progress,
        )

    if kind == "pads":
        count = event.get("count")
        return replace(assignment, pads=count if isinstance(count, int) else assignment.pads)

    if kind == "progress":
        frac = event.get("frac")
        return replace(assignment, progress=float(frac) if isinstance(frac, (int, float)) else 0.0)

    if kind == "claim":
        player = event.get("player")
        if not isinstance(player, int):
            return assignment
        seat = Seat(
            player=player,
            name=str(event.get("name") or ""),
            icon=str(event.get("icon") or ""),
            configured=bool(event.get("configured", True)),
        )
        others = tuple(s for s in assignment.seats if s.player != player)
        return replace(
            assignment,
            seats=tuple(sorted((*others, seat), key=lambda s: s.player)),
            progress=0.0,
            message="",
        )

    if kind == "confirm":
        frac = event.get("frac")
        return replace(assignment, confirm=float(frac) if isinstance(frac, (int, float)) else 0.0)

    if kind == "accepted":
        return replace(assignment, finished=True, progress=0.0, confirm=0.0, message="")

    if kind == "error":
        return replace(assignment, message=str(event.get("message") or "padmap said no"))

    return assignment


@dataclass
class Session:
    """The screen's side of an assignment: what to send, and what to show.

    Holds the commands as well as the state so that the picker's event loop
    stays a loop -- it hands over a key press and gets back whatever should be
    sent, rather than knowing padmap's vocabulary itself.
    """

    slots: int = 4
    view: Assignment = field(default_factory=Assignment)
    open: bool = False

    def begin(self) -> dict:
        self.open = True
        self.view = Assignment(slots=self.slots, state="assigning")
        return {"cmd": "begin", "players": self.slots}

    def accept(self) -> dict:
        return {"cmd": "accept"}

    def cancel(self) -> dict:
        self.open = False
        self.view = Assignment(slots=self.slots)
        return {"cmd": "cancel"}

    def reset(self) -> dict:
        self.view = replace(self.view, seats=(), progress=0.0, finished=False)
        return {"cmd": "reset"}

    def leave(self) -> dict:
        """Out of this screen, keeping whatever has been claimed.

        B used to cancel, and cancel is padmap throwing every claim away: the
        clones go with them, and a picker that only answers published pads has
        just lost the controller that pressed B. So anything claimed is kept --
        that is what `accept` is -- and cancel is left for the case where there
        is genuinely nothing to keep.
        """
        self.open = False
        seated = bool(self.view.seats)
        self.view = Assignment(slots=self.slots)
        return {"cmd": "accept"} if seated else {"cmd": "cancel"}

    def handle(self, event: dict) -> None:
        """One event from padmap, and what it does to the screen.

        `open` follows the daemon rather than only this program's own `begin`.
        padmap opens a session by itself for a pad it has no mapping for, and
        for the length of one it holds every pad -- so a picker that did not
        notice sat on the grid answering no button, with nothing on screen to
        say why or how to get out.
        """
        self.view = apply(self.view, event)
        if event.get("event") == "state":
            self.open = event.get("state") == "assigning"
        if self.view.finished:
            self.open = False


@dataclass
class Watch:
    """Keeping padmap listening for a hold, for as long as the picker is up.

    `seating` rather than `begin`: a session grabs every pad and owns the
    screen, which is right when somebody asked to set controllers up and wrong
    for a library that is only waiting for the first person to pick a pad up.
    Seating grabs nothing, claims only free seats, and is what makes "plug one
    in and hold a button" into player one without leaving the grid.

    padmap does not acknowledge the command, so there is nothing to read back:
    it is sent again on each connection and after each change of state, which
    the daemon takes idempotently. Not every frame, which would be a syscall
    sixty times a second to tell a daemon what it already knows.

    Never closed. The daemon keeps seating open after this client is gone, so
    a pad picked up in the middle of a game takes the next free seat exactly
    as one picked up in front of the grid does -- and the game is where the
    second player usually turns up.
    """

    slots: int = 4
    # The (state, seated) it was last asked under. None is "not asked", which
    # is where a lost connection puts it: a restarted daemon remembers nothing.
    asked: tuple[str, int] | None = None
    # A daemon too old to know the command. It is never asked again -- and
    # that is all: the pads are not handed back to whoever holds them. A
    # machine whose padmap cannot seat anybody is a machine the keyboard
    # drives, until padmap is fixed.
    refused: bool = False

    def handle(self, event: dict) -> None:
        """padmap's answer, when it has one. Only a refusal says anything: the
        daemon acknowledges `seating` with silence, and names the command it
        did not understand. Matched whole, because other errors mention
        seating too and none of them mean this."""
        if event.get("event") == "error" and str(event.get("message") or "") == 'unknown command "seating"':
            self.refused = True

    def wanted(self, connected: bool, state: str, seated: int) -> dict | None:
        """The command to send now, or None when padmap is already listening."""
        if self.refused:
            return None
        if not connected:
            self.asked = None
            return None
        # Inside a session padmap suspends seating, and the assignment screen
        # is asking for the same holds anyway. Full seats are the same shape of
        # nothing-to-do. Both forget what was asked rather than keeping it: a
        # fourth player who unplugs puts the state back to a tuple that was
        # already sent, and a `wanted` that only compares would then never
        # mention the seat they freed.
        if state == "assigning" or seated >= self.slots:
            self.asked = None
            return None
        here = (state, seated)
        if here == self.asked:
            return None
        self.asked = here
        return {"cmd": "seating", "open": True, "players": self.slots}



def attend(padmap, seating: Session, watch: Watch) -> dict | None:
    """One frame of keeping up with padmap.

    What the daemon has said is folded in, and padmap is told to go on
    listening for a hold if it needs telling. Who may move the cursor is not
    decided here or anywhere: a pad padmap published, and nothing else, and
    `pads.py` asks `clones.py` on every event.

    Returns the command to send, if any. Nothing is sent from here: the
    caller owns the socket.
    """
    for event in padmap.poll():
        if trace.on() and event.get("event") != "progress":
            trace.say("padmap", **{k: v for k, v in event.items() if k not in ("lines", "build")})
        seating.handle(event)
        watch.handle(event)
    command = watch.wanted(padmap.connected, padmap.status_word, len(padmap.players))
    if command is not None:
        trace.say("sent", **command)
    return command
