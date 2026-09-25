"""Who is holding a button to pair right now, and in what order they started.

One person pairing is a fraction. Two people pairing at once is a queue, and
the screen has to show both of them -- each pad filling in on its own, in the
order the buttons went down, because that is the order the seats go out in
and somebody watching has to be able to see they are second.

danstick sends `{"event": "progress", "frac": 0.42}` per pad per tick and, at
the time of writing, without saying which pad: two holds arrive as one
fraction jumping between two values. `docs/requests/two-people-pairing-at-once.md`
asks for the pad's name on it. This reads either -- a named reading is its own
fill, an anonymous one is the single fill it has always been -- so the screen
is right the day that lands and not wrong before it.

Nothing here has a clock of its own. The loop passes the time it already has,
which is what makes a queue of holds testable without sleeping through one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# How long a reading is believed. For a reading with no pad on it, silence is
# still the only sign a hold ended -- an old daemon says nothing when a button
# comes up -- so three frames of it, the same rule `gate.Fade` follows.
STALE = 0.05

# And for a named one, a safety net rather than a signal. danstick names the pad
# on every reading now and says `frac: 0` for that pad when it is let go, so a
# release is never inferred from silence. Inferring it anyway was the flash:
# danstick sends progress from the same loop that rescans every device once a
# second, a rescan outlasts fifty milliseconds, and the hold was dropped
# mid-press -- the red empty seat for a frame or three, then the controller
# again, once a second, for as long as somebody held on. This long only
# catches a daemon that died with a button down.
NAMED_STALE = 0.5

# The key an anonymous reading is filed under. A daemon that does not name its
# pads can only ever describe one hold, and that is the one.
ANYBODY = ""


@dataclass
class Hold:
    """One pad's hold: how far through, since when, and last heard of when."""

    fraction: float = 0.0
    started: float = 0.0
    seen: float = 0.0
    player: int | None = None
    name: str = ""
    # The furthest this hold has been drawn. Between readings the fill is
    # carried forward on this program's clock, and the next reading can land
    # a hair behind it -- the edge of the sweep ticking back a pixel, which on
    # a reveal that is otherwise one smooth movement is the thing you see.
    drawn: float = 0.0


@dataclass
class Joining:
    """Every hold in flight, oldest press first."""

    holds: dict[str, Hold] = field(default_factory=dict)
    hold_seconds: float = 0.0
    # Whether this daemon names its readings. Once it has, the anonymous
    # single fill describes a hold this queue is already drawing, and a
    # screen that drew both drew the same press twice, in two places, taking
    # turns -- see `anonymous`.
    named: bool = False

    def saw(self, event: dict, now: float) -> None:
        """One `progress` event, named or not."""
        if event.get("event") != "progress":
            return
        frac = event.get("frac")
        if not isinstance(frac, (int, float)):
            return
        key = str(event.get("node") or event.get("name") or ANYBODY)
        if key != ANYBODY:
            self.named = True
        player = event.get("player")
        found = self.holds.get(key)
        if frac <= 0:
            # A release said out loud, which is the one thing silence cannot
            # tell apart when two pads are holding.
            self.holds.pop(key, None)
            return
        if found is None or frac < found.fraction - 1e-6:
            # A new hold, or the same pad starting again after letting go.
            # Any decrease at all: danstick's fraction is elapsed over the hold
            # length, so it only ever climbs within one press -- a smaller
            # number is a different press, and that press is at the back of
            # the queue however small the gap was.
            self.holds[key] = Hold(
                fraction=float(frac),
                started=now,
                seen=now,
                player=player if isinstance(player, int) else None,
                name=str(event.get("name") or ""),
            )
            return
        found.fraction = float(frac)
        found.seen = now
        if isinstance(player, int):
            found.player = player
        if event.get("name"):
            found.name = str(event["name"])

    def now(self, now: float) -> list[Hold]:
        """The holds to draw, oldest press first.

        Carried forward between readings at the rate the hold implies -- the
        readings are every 20 ms and the screen draws every 16 -- and dropped
        when they stop coming, which is how a button coming up looks from
        here.
        """
        for key, hold in list(self.holds.items()):
            if now - hold.seen > (STALE if key == ANYBODY else NAMED_STALE):
                del self.holds[key]
        out = []
        for hold in self.holds.values():
            carried = hold.fraction
            if self.hold_seconds > 0:
                carried = min(1.0, carried + (now - hold.seen) / self.hold_seconds)
            carried = max(carried, hold.drawn)
            hold.drawn = carried
            out.append(
                Hold(
                    fraction=carried,
                    started=hold.started,
                    seen=hold.seen,
                    player=hold.player,
                    name=hold.name,
                )
            )
        return sorted(out, key=lambda hold: hold.started)

    def anonymous(self, fraction: float) -> float:
        """The single, nameless fill to draw alongside this queue: `fraction`
        from a daemon that does not name its pads, and nothing from one that
        does -- its holds are all in the queue already."""
        return 0.0 if self.named else fraction

    def seated(self, players: list | None) -> None:
        """A `state` says who is in a seat; their hold is over, nobody else's.

        This used to be `clear()`, and it is the whole of "the controllers
        flash back to empty". danstick restates the world while a button is
        still down -- a pad arriving, a republish, its own tick -- and wiping
        the queue on every one of those threw away a fill that was half way
        up. The next `progress` twenty milliseconds later built it again from
        scratch, so the drawing blinked out and started over, several times
        in a hold. What a state actually settles is who is *seated*: those
        holds are finished, and a hold still climbing towards a free seat is
        none of its business.
        """
        taken: set[int] = set()
        names: set[str] = set()
        nodes: set[str] = set()
        for player in players or []:
            if not isinstance(player, dict):
                continue
            if isinstance(player.get("player"), int):
                taken.add(player["player"])
            if player.get("name"):
                names.add(str(player["name"]))
            if player.get("node"):
                nodes.add(str(player["node"]))
        for key, hold in list(self.holds.items()):
            if key:
                # By identity wherever there is one. The seat number on a
                # reading is the seat the hold is *filling towards*, and it
                # goes stale the moment somebody else's claim lands: between
                # that claim and danstick recomputing, the second person's
                # reading still names seat one. Dropping on that number would
                # blink the fill of the very person this queue exists for.
                gone = key in nodes or key in names or (hold.name and hold.name in names)
            else:
                # An anonymous reading describes one hold and cannot say
                # whose, so the seat number is all there is.
                gone = hold.player in taken
            if gone:
                del self.holds[key]

    def clear(self) -> None:
        """Everything let go: a claim has landed and the state is the truth."""
        self.holds.clear()
