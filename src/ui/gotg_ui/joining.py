"""Who is holding a button to pair right now, and in what order they started.

One person pairing is a fraction. Two people pairing at once is a queue, and
the screen has to show both of them -- each pad filling in on its own, in the
order the buttons went down, because that is the order the seats go out in
and somebody watching has to be able to see they are second.

padmap sends `{"event": "progress", "frac": 0.42}` per pad per tick and, at
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

# How long a reading is believed. padmap sends them about every 20 ms while a
# button is down and says nothing at all when it comes up, so silence is the
# only signal a hold ended -- three frames of it, the same rule `gate.Fade`
# follows for the single fill.
STALE = 0.05

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


@dataclass
class Joining:
    """Every hold in flight, oldest press first."""

    holds: dict[str, Hold] = field(default_factory=dict)
    hold_seconds: float = 0.0

    def saw(self, event: dict, now: float) -> None:
        """One `progress` event, named or not."""
        if event.get("event") != "progress":
            return
        frac = event.get("frac")
        if not isinstance(frac, (int, float)):
            return
        key = str(event.get("node") or event.get("name") or ANYBODY)
        player = event.get("player")
        found = self.holds.get(key)
        if frac <= 0:
            # A release said out loud, which is the one thing silence cannot
            # tell apart when two pads are holding.
            self.holds.pop(key, None)
            return
        if found is None or frac < found.fraction - 1e-6:
            # A new hold, or the same pad starting again after letting go.
            # Any decrease at all: padmap's fraction is elapsed over the hold
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
            if now - hold.seen > STALE:
                del self.holds[key]
        out = []
        for hold in self.holds.values():
            carried = hold.fraction
            if self.hold_seconds > 0:
                carried = min(1.0, carried + (now - hold.seen) / self.hold_seconds)
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

    def clear(self) -> None:
        """Everything let go: a claim has landed and the state is the truth."""
        self.holds.clear()
