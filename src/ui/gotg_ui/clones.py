"""Which pads are danstick's, and which are the machine's own.

danstick grabs a physical pad and republishes it through uinput as "danstick
Player N", so while it is running both exist: the original, silent under
EVIOCGRAB, and the clone that is the one with a seat. Three things spoil that
silence -- a grab that failed, the 2026 Steam Controller, which danstick cannot
grab at all, and danstick's own seating mode, which listens for a hold while
grabbing nothing. In each of them a raw pad's presses arrive here as well, and
a picker that acts on them is taking orders from a controller nobody has
assigned.

So: the clone or nothing. A pad moves the cursor once danstick has published it,
and until then the only thing a press on it does is claim a seat -- which
danstick reads from the device itself, not from anything this program sees.

The names are danstick's, from `danstick-core/src/emit.rs` and
`danstick-input/src/pad.rs`, and the name test is the same as its
`is_danstick_clone`. Not by vendor and product: a clone mirrors its source's ids
by default, and 1209:0001 is danstick's only under DANSTICK_PAD_IDENTITY=danstick,
which is not how this machine runs it.

The name alone is not enough either, which cost this feature a day. SDL looks
a device's vendor and product up in its own database and answers with *that*
name: a clone of an Xbox pad mirrors 045e:028e, so SDL calls it "Xbox 360
Controller" and the kernel's "danstick Player 3" never reaches a front-end. The
GUID is what survives -- SDL takes a CRC of the real name before it renames
anything, and puts it in bytes 2 and 3. So: the name when it is there, and the
CRC underneath it when SDL has papered over it. Measured on a live daemon, and
the GUIDs in the tests are the ones it published.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# "danstick Player 1". The prefix is danstick's VIRTUAL_PREFIX, and the phys one
# its VIRTUAL_PHYS_PREFIX -- set best-effort, since UI_SET_PHYS can fail and
# danstick then builds the device without one.
VIRTUAL_PREFIX = "danstick Player "
VIRTUAL_PHYS_PREFIX = "danstick/"

# How many seats to recognise a clone for. danstick will seat as many as it is
# asked for; this is the range of names whose CRC is worth knowing, and four
# players is what the picker asks for with room to spare.
SEATS = 8


def crc16(data: bytes) -> int:
    """SDL's own CRC-16, which is the reflected ARC one. `SDL_crc16`."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


# The CRC SDL would have taken of each clone's real name, by player. Worked out
# once: the alternative is sixteen CRCs per pad event.
CLONE_CRCS = frozenset(crc16(f"{VIRTUAL_PREFIX}{player}".encode()) for player in range(1, SEATS + 1))

# And which player each of those CRCs is. The seat number is in the clone's
# real name, so a press can be attributed to the person who made it -- which
# is what lets a screen show *who* is holding a button rather than only that
# somebody is.
CLONE_PLAYERS = {crc16(f"{VIRTUAL_PREFIX}{player}".encode()): player for player in range(1, SEATS + 1)}


def name_crc(guid: str | None) -> int | None:
    """The CRC of the name SDL first saw, out of a GUID, or None if that is
    not a GUID. Bytes 2 and 3, little-endian, as SDL writes them."""
    raw = str(guid or "")
    if len(raw) < 8:
        return None
    try:
        pair = bytes.fromhex(raw[4:8])
    except ValueError:
        return None
    return pair[0] | (pair[1] << 8)


def is_clone(name: str | None, phys: str | None = "", guid: str | None = "") -> bool:
    """Whether this is a pad danstick published, rather than one it grabbed."""
    if str(phys or "").startswith(VIRTUAL_PHYS_PREFIX) or str(name or "").startswith(VIRTUAL_PREFIX):
        return True
    return name_crc(guid) in CLONE_CRCS


def player_of(name: str | None, phys: str | None = "", guid: str | None = "") -> int | None:
    """Which seat this pad is, or None if it is not one of danstick's.

    Three ways of asking the same question, in the order they survive: the
    kernel name danstick gave the clone, the phys it set beside it, and the CRC
    SDL took of that name before renaming the device out from under it.
    """
    text = str(name or "")
    if text.startswith(VIRTUAL_PREFIX):
        tail = text[len(VIRTUAL_PREFIX):].strip()
        if tail.isdigit():
            return int(tail)
    port = str(phys or "")
    if port.startswith(VIRTUAL_PHYS_PREFIX):
        tail = port[len(VIRTUAL_PHYS_PREFIX):].lstrip("pP")
        if tail.isdigit():
            return int(tail)
    return CLONE_PLAYERS.get(name_crc(guid))


def drives(name: str | None, phys: str | None = "", guid: str | None = "") -> bool:
    """Whether this pad may move the picker's cursor: only if danstick published it.

    No other case. There used to be two -- no daemon on the machine, and a
    daemon too old to seat anybody -- on the theory that a picker nothing
    could drive was worse than one anything could. It was not: a shell that
    had not reloaded since danstick joined its PATH looked exactly like "no
    danstick here", and an unassigned Xbox pad drove the library, which is the
    one thing this exists to stop. The keyboard always works; that is the
    fallback.

    GOTG_ANY_PAD=1 lifts the rule by hand, for a television with no keyboard
    in the room. Off unless somebody typed it.
    """
    if os.environ.get("GOTG_ANY_PAD") == "1":
        return True
    return is_clone(name, phys, guid)


@dataclass
class Owners:
    """Which of the pads SDL has open are danstick's, by instance id.

    No pygame here on purpose. `pads.py` holds the half that needs a screen --
    opening a device, and digging the instance id out of an event -- and this
    is the half worth testing: what was opened, what it was called, and whether
    it may move anything.

    An id this has never been told about is not danstick's. That is the safe
    answer and the common one: it is what a pad that failed to open looks like,
    and what an event from a device that was unplugged and forgotten looks
    like.
    """

    names: dict[int, str] = field(default_factory=dict)
    guids: dict[int, str] = field(default_factory=dict)

    def opened(self, instance: int, name: str, guid: str = "") -> None:
        self.names[instance] = name or ""
        self.guids[instance] = guid or ""

    def closed(self, instance: int) -> None:
        """Forgotten outright rather than left behind: the kernel reuses an
        instance id, and a stale "danstick Player 2" against a number now
        belonging to somebody's raw pad is the whole rule inverted."""
        self.names.pop(instance, None)
        self.guids.pop(instance, None)

    def may_drive(self, instance: int | None) -> bool:
        """Whether an event from this pad may move the picker."""
        return drives(self.names.get(instance, ""), guid=self.guids.get(instance, ""))

    def player(self, instance: int | None) -> int | None:
        """Which player's pad this is, for a screen that shows who pressed."""
        if instance not in self.names:
            return None
        return player_of(self.names.get(instance, ""), guid=self.guids.get(instance, ""))
