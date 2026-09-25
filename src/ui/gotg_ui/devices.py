"""What a seated pad really is, for when its name will not say.

A Steam Deck's built-in controls report `N: Name="Valve Software Steam
Controller"` -- character for character what a Steam Controller Puck reports,
because the Deck presents the same Valve HID interface. So the name rules in
`config/icons.yaml` cannot tell a handheld somebody is holding from a pad on
the table in front of a television, and the `"steam deck"` rule written for it
can never fire on real hardware. Only the product id separates them: 0x1205 is
a Deck, 0x1102 and 0x1142 the wired and wireless Steam Controllers, 0x1304 the
Puck.

danstick names the device node of every seat it hands out (`players[].node`),
and that is the hook. `/proc/bus/input/devices` carries `Vendor` and `Product`
beside every node, and `hush.parse` already reads that file for the grabs, so
this is one dictionary on top of it. A clone under the default `mirror`
identity copies its source's vendor and product, so the answer is the same
whether the node named is the pad itself or danstick's republished copy of it --
under `xbox360` (the decompiled ports only, never the picker) it would be
Microsoft's, which is what that identity is for.

Cached, because the strip is redrawn sixty times a second and this is a ten
kilobyte file the kernel does not rewrite under a live node. `forget()` drops
it, and the picker calls that where it already refreshes the grabs: when SDL
says a device arrived or left.
"""

from __future__ import annotations

import time

from .hush import DEVICES, parse

# How long a miss is believed before the file is read again. A hit is never
# re-read at all: a node's ids do not change while the node exists. The window
# only bounds how long a pad that appeared without SDL noticing draws as the
# generic pad.
CACHE_SECONDS = 1.0

# Where a hidraw node's ids are written. A Steam Controller and a Deck have no
# joystick evdev node at all -- the pad is hidraw-only, and danstick reads it
# there (danstick's docs/HIDRAW.md) -- so a seat on one names `/dev/hidrawN`,
# which /proc/bus/input/devices has never heard of. sysfs has: one `uevent`
# per node carrying `HID_ID=<bus>:<vendor>:<product>`, all eight digits wide.
HIDRAW = "/sys/class/hidraw"

_ids: dict[str, str] = {}
_read_at: float = 0.0


def node_key(node: str | None) -> str:
    """`event9` out of whatever form of it danstick sent.

    danstick says `/dev/input/event9`; the file says `event9`; a keyboard seat
    says nothing at all, and so does a seat danstick published before it had a
    node to name.
    """
    text = str(node or "").strip()
    if not text:
        return ""
    return text.rsplit("/", 1)[-1]


def _hidraw_ids(key: str, root: str = HIDRAW) -> str | None:
    """`28de:1205` for a hidraw node, out of sysfs."""
    try:
        with open(f"{root}/{key}/device/uevent") as uevent:
            text = uevent.read()
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("HID_ID="):
            continue
        parts = line.split("=", 1)[1].split(":")
        if len(parts) == 3:
            try:
                return f"{int(parts[1], 16):04x}:{int(parts[2], 16):04x}"
            except ValueError:
                return None
    return None


def _refresh(text: str | None = None) -> dict[str, str]:
    """Every event node the kernel lists, as `vendor:product`."""
    if text is None:
        try:
            with open(DEVICES) as devices:
                text = devices.read()
        except OSError:
            # No /proc to read (a sandbox, another kernel): every pad is known
            # by its name, which is what happened before this file existed.
            return {}
    return {node.event: f"{node.vendor:04x}:{node.product:04x}" for node in parse(text) if node.event}


def ids_for(node: str | None, text: str | None = None, hidraw_root: str = HIDRAW) -> str | None:
    """`28de:1205` for the node this seat reads, or None when it is unknown.

    `text` is the device file's contents and `hidraw_root` a copy of sysfs,
    both for tests: passed, nothing is cached and nothing real is read, so a
    fixture answers exactly as itself.
    """
    key = node_key(node)
    if not key:
        return None

    global _ids, _read_at

    if key.startswith("hidraw"):
        if hidraw_root != HIDRAW:
            return _hidraw_ids(key, hidraw_root)
        if key not in _ids:
            found = _hidraw_ids(key)
            if found is None:
                return None
            _ids[key] = found
        return _ids[key]

    if text is not None:
        return _refresh(text).get(key)

    if key not in _ids and time.monotonic() - _read_at > CACHE_SECONDS:
        _ids = _refresh()
        _read_at = time.monotonic()
    return _ids.get(key)


def forget() -> None:
    """Read the device list again on the next question.

    Node numbers are reused: unplug the Deck's dock and plug in a pad, and
    `event9` can be a different device wearing the same name. Called where the
    picker already reacts to a device arriving or leaving.
    """
    global _ids, _read_at
    _ids = {}
    _read_at = 0.0
