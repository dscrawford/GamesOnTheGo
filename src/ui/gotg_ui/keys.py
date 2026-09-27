"""Whether the keyboard may drive anything, which is now danstick's answer too.

The keyboard used to be the one thing that always worked. A pad had to be
published before it could move the cursor -- `clones.py` -- and the keyboard
was the fallback underneath that rule: no daemon, no seat, no problem.

It was also the hole the rule was built to close. A controller is a keyboard
in hardware (a Steam Controller in lizard mode types Enter when A is pressed,
an Xbox pad over Bluetooth carries a keyboard collection), so "anything that
types" included controllers nobody had assigned. `hush.py` holds the nodes it
can see, and that is a list of devices rather than a principle. The principle
is this one: **the keyboard takes a seat like everything else**, and until it
has, the only key it can send is the one that takes the seat.

So `drives` is the keyboard's `clones.drives`. It says yes when danstick reports
a keyboard seat -- which danstick gives after a long hold on the space bar,
reading the key itself wherever the person is -- and no otherwise. `pairing` is the exception
that keeps it usable: the space bar is always heard, because a keyboard that
cannot ask for a seat cannot be given one.

`GOTG_ANY_PAD=1` lifts this the way it lifts the pad rule, for a machine where
danstick cannot run at all. Nothing sets it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import config

# danstick's own word for the seat it hands the keyboard. The name is what a
# `claim` carries; `keyboard: true` is what a `state` carries, and both are
# checked because a front-end that reads only one of them is reading half of
# what the daemon says.
KEYBOARD = "keyboard"


def seated(players: list | None) -> bool:
    """Whether danstick has given the keyboard a seat."""
    for player in players or []:
        if not isinstance(player, dict):
            continue
        if player.get("keyboard") is True:
            return True
        if str(player.get("name") or "").strip().lower() == KEYBOARD:
            return True
        if str(player.get("icon") or "").strip().lower() == KEYBOARD:
            return True
    return False


def seat_of(players: list | None) -> int | None:
    """Which player the keyboard is, if danstick has seated it.

    The door needs the number: everybody seated has to ready up, and a
    keyboard that cannot hold A would hold the room for ever.
    """
    for player in players or []:
        if not isinstance(player, dict) or not isinstance(player.get("player"), int):
            continue
        if player.get("keyboard") is True:
            return player["player"]
        if str(player.get("name") or "").strip().lower() == KEYBOARD:
            return player["player"]
        if str(player.get("icon") or "").strip().lower() == KEYBOARD:
            return player["player"]
    return None


def drives(players: list | None, connected: bool = True) -> bool:
    """Whether a key press may move this screen.

    Not "is danstick here": a daemon that is missing or down is exactly what an
    unassigned controller typing into the picker looks like from in here, and
    the pad rule learned that the hard way. The one way out is by hand.
    """
    if os.environ.get("GOTG_ANY_PAD") == "1":
        return True
    return bool(connected) and seated(players)


# How long the space bar is held to take a seat with the keyboard. The same
# second and a half as every other hold -- a tap on space opens the game menu,
# and the two must not be one motion apart.
KEYBOARD_HOLD = float(config.get("theme.timeouts.keyboard_hold", 1.5))


@dataclass
class KeyHold:
    """The space bar, and whether letting it go was a tap.

    Held, it seats the keyboard as a player -- danstick does that itself,
    reading the space bar wherever the person is, game included (danstick
    e0092be). What is left here is the other half of one key's two meanings:
    released early it is the tap it always was -- open the menu -- and
    released after the hold's length it was a seat, and nothing.
    """

    seconds: float = KEYBOARD_HOLD
    since: float | None = None

    def down(self, now: float) -> None:
        if self.since is None:
            self.since = now

    def progress(self, now: float) -> float:
        """How far along the hold is, 0 when nothing is held.

        Whole at a millisecond short of the time: this is compared to 1.0,
        and 0.6 seconds of floating point is not always 0.6.
        """
        if self.since is None:
            return 0.0
        elapsed = now - self.since
        return 1.0 if elapsed >= self.seconds - 1e-3 else max(0.0, elapsed / self.seconds)

    def up(self, now: float) -> bool:
        """The key released. True when it was a tap and the menu should open."""
        tap = self.since is not None and self.progress(now) < 1.0
        self.since = None
        return tap
