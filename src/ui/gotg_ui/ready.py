"""Readying up, per person, because a room has more than one.

Everybody seated holds A for three seconds; when the last of them is ready,
the game starts. That is one rule with two halves, and the first version of
it had neither.

It had one hold for the whole room: whoever pressed first owned it and
everybody else's press was ignored. And the pause that keeps the pairing
press from rolling into the go -- see `seat.PAUSE` -- was measured across
every pad at once, so it only ever ended when *nothing* was down anywhere.
Two people holding A locked each other out for good: one holds, so the room
is never quiet, so the other's press never counts; the first lets go, and the
other is still holding, so it still never counts. Neither could ready up and
neither could see why.

So it is per seat here. Each player has a quiet of their own, a hold of their
own, and readiness that stays once it is earned -- somebody who readied up
first should not have to keep holding while the others catch up. Nothing has
a clock: the loop passes the time and the set of seats with a button down,
which is what makes a four-player deadlock a unit test rather than an evening.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ready:
    """Who is holding, how far through, and who is already ready."""

    seconds: float = 3.0
    pause: float = 1.0
    # Since when this seat has had nothing down. None means something is.
    quiet: dict[int, float] = field(default_factory=dict)
    busy: set[int] = field(default_factory=set)
    # Since when this seat's go hold has been counting.
    since: dict[int, float] = field(default_factory=dict)
    ready: set[int] = field(default_factory=set)

    def tick(self, holding: set[int], seats: set[int], now: float) -> str | None:
        """What is down right now, per seat. Returns a seat that just readied.

        Three things in one place because they are one thing: a hold that has
        reached its length becomes ready, a seat with nothing down starts its
        pause, and a hold nobody is holding stops counting. That last one is
        not paranoia -- a release can go missing when padmap republishes a
        clone, and a hold that kept its start time finished three seconds
        later with nobody holding anything.
        """
        self.busy = set(holding)
        just = self.settle(now)
        for player in seats:
            if player in holding:
                self.quiet.pop(player, None)
            else:
                self.quiet.setdefault(player, now)
                self.since.pop(player, None)
        self.forget(set(seats))
        return ", ".join(str(player) for player in sorted(just)) or None

    def settled(self, player: int, now: float) -> bool:
        """Whether this seat has been quiet long enough for a press to count.

        Per seat: the press that paired *this* controller is the one that must
        not roll into its go, and what somebody else is doing has nothing to
        say about it. Measured across the room, as it was, two people holding
        meant the room was never quiet and nobody could start at all.
        """
        since = self.quiet.get(player)
        return since is not None and now - since >= self.pause

    def pressed(self, player: int, now: float) -> None:
        """A go button, on this seat's pad."""
        if player in self.ready or player in self.since:
            return
        if not self.settled(player, now):
            return
        self.since[player] = now

    def progress(self, player: int, now: float) -> float:
        """How far through this seat's hold, 0 to 1. Ready is 1."""
        if player in self.ready:
            return 1.0
        started = self.since.get(player)
        if started is None:
            return 0.0
        if self.seconds <= 0:
            return 1.0
        return min(1.0, max(0.0, (now - started) / self.seconds))

    def settle(self, now: float) -> set[int]:
        """Promote finished holds to ready. Returns who just became ready."""
        done = {
            player
            for player, started in self.since.items()
            if self.seconds <= 0 or now - started >= self.seconds - 1e-3
        }
        for player in done:
            self.since.pop(player, None)
            self.ready.add(player)
        return done

    def all_ready(self, seats: set[int]) -> bool:
        """Whether every seat in the room has readied up.

        An empty room is not ready: the door is there because somebody is
        playing, and "nobody is here, so everybody is ready" starts a game
        with no controllers in it.
        """
        return bool(seats) and seats <= self.ready

    def forget(self, seats: set[int]) -> None:
        """Seats that have gone take their readiness with them."""
        self.ready &= seats
        for player in list(self.since):
            if player not in seats:
                del self.since[player]
