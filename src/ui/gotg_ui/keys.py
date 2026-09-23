"""Whether the keyboard may drive anything, which is now padmap's answer too.

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

So `drives` is the keyboard's `clones.drives`. It says yes when padmap reports
a keyboard seat -- `seat_keyboard`, which the picker and the gate send after a
long hold on the space bar -- and no otherwise. `pairing` is the exception
that keeps it usable: the space bar is always heard, because a keyboard that
cannot ask for a seat cannot be given one.

`GOTG_ANY_PAD=1` lifts this the way it lifts the pad rule, for a machine where
padmap cannot run at all. Nothing sets it.
"""

from __future__ import annotations

import os

# padmap's own word for the seat it hands the keyboard. The name is what a
# `claim` carries; `keyboard: true` is what a `state` carries, and both are
# checked because a front-end that reads only one of them is reading half of
# what the daemon says.
KEYBOARD = "keyboard"


def seated(players: list | None) -> bool:
    """Whether padmap has given the keyboard a seat."""
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
    """Which player the keyboard is, if padmap has seated it.

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

    Not "is padmap here": a daemon that is missing or down is exactly what an
    unassigned controller typing into the picker looks like from in here, and
    the pad rule learned that the hard way. The one way out is by hand.
    """
    if os.environ.get("GOTG_ANY_PAD") == "1":
        return True
    return bool(connected) and seated(players)
