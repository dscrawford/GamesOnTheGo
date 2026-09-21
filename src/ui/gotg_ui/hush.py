"""Controllers that are also keyboards, and keeping them quiet.

The joystick rule in `clones.py` holds: a pad padmap has not published cannot
move the cursor through SDL's joystick or controller events. Then a Steam
Controller moved it anyway, and so did an Xbox pad over Bluetooth -- because
neither reached SDL as a joystick. The Steam Controller Puck is, in hardware,
four keyboards and four mice (lizard mode: the d-pad is arrow keys, A is
Enter, the trackpad is a mouse) until something opens its hidraw and tells it
to stop. An Xbox pad over Bluetooth carries `Keyboard`, `Mouse` and `Consumer
Control` collections of its own. The picker takes the keyboard and the mouse
on purpose, and cannot tell those from a real one: SDL delivers a key, not the
device it came from.

So this works one layer down. Every input node that is a keyboard or a mouse
*and belongs to a controller* is opened and held with EVIOCGRAB for as long as
the picker runs. A grab is exclusive: the compositor stops receiving from that
node, so no key or motion from it reaches SDL, and the grab is released when
the fd closes -- on exit, or on a crash. Real keyboards are never touched; the
rule needs a controller to point at.

Which nodes are a controller's is decided from /proc/bus/input/devices, with
no kernel calls, so it can be tested against a copy of that file -- and the
copy in the tests is the one this machine produced with both pads attached.

padmap turns lizard mode off itself when it opens a Puck, and that is the
better fix for that half; this covers the moment before it has, the machine
where it has not started, and the Bluetooth pad's extra collections, which
nobody turns off.
"""

from __future__ import annotations

import fcntl
import os
import sys
from dataclasses import dataclass, field

from . import trace

DEVICES = "/proc/bus/input/devices"

# Valve. A Steam Controller's lizard keyboard and mouse carry its vendor id
# and no joystick sibling at all -- the pad itself is hidraw-only -- so they
# are known by who made them.
VALVE = 0x28DE

# BUS_BLUETOOTH. Over Bluetooth every device's phys is the *adapter's*
# address, so two devices sharing one means only that they reached the same
# radio -- the pad, and the phone's media keys. Uniq is the device's own.
BLUETOOTH = 0x05

# _IOW('E', 0x90, int): EVIOCGRAB.
EVIOCGRAB = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x90


@dataclass(frozen=True)
class Node:
    """One entry of /proc/bus/input/devices, the parts that matter."""

    name: str = ""
    bus: int = 0
    vendor: int = 0
    product: int = 0
    phys: str = ""
    uniq: str = ""
    handlers: tuple[str, ...] = ()

    @property
    def event(self) -> str | None:
        """The /dev/input/eventN this node is read through, if any."""
        for handler in self.handlers:
            if handler.startswith("event"):
                return handler
        return None

    @property
    def is_joystick(self) -> bool:
        return any(h.startswith("js") for h in self.handlers)

    @property
    def is_keyboard_or_mouse(self) -> bool:
        return any(h == "kbd" or h.startswith("mouse") for h in self.handlers)


def parse(text: str) -> list[Node]:
    """Every device the kernel lists, as the file says it."""
    nodes: list[Node] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if len(line) > 2 and line[1] == ":":
                fields[line[0]] = line[3:]
        if "N" not in fields:
            continue
        ids = dict(part.split("=", 1) for part in fields.get("I", "").split() if "=" in part)
        nodes.append(
            Node(
                name=fields["N"].split('"')[1] if '"' in fields["N"] else fields["N"].strip(),
                bus=int(ids.get("Bus", "0"), 16),
                vendor=int(ids.get("Vendor", "0"), 16),
                product=int(ids.get("Product", "0"), 16),
                phys=fields.get("P", "").replace("Phys=", "").strip(),
                uniq=fields.get("U", "").replace("Uniq=", "").strip(),
                handlers=tuple(fields.get("H", "").replace("Handlers=", "").split()),
            )
        )
    return nodes


def a_controllers(nodes: list[Node]) -> list[Node]:
    """The keyboard and mouse nodes that belong to a controller.

    Two ways to belong. Sharing a unique id with a node that is a joystick --
    or a phys, off Bluetooth, where a phys is a port rather than a radio:
    that is the Xbox pad, whose `Keyboard` and `Mouse` carry the same address
    as its `js0`. Or being Valve's: a Steam Controller's lizard mode has no
    joystick sibling to point at, because the pad is hidraw-only, and is
    known by its vendor instead.

    A real keyboard fails both. The Logitech receiver's keyboard shares a
    phys with the Logitech receiver's mouse, and neither is a joystick. And
    the phone's media keys, arriving through BlueZ on the same adapter as
    the pad, share its phys and nothing else -- which is why phys alone was
    not enough, and the fixture says so.
    """
    joysticks = [n for n in nodes if n.is_joystick]
    uniqs = {n.uniq for n in joysticks if n.uniq}
    physes = {n.phys for n in joysticks if n.phys and n.bus != BLUETOOTH}
    out = []
    for node in nodes:
        if not node.is_keyboard_or_mouse or node.event is None:
            continue
        by_uniq = bool(node.uniq) and node.uniq in uniqs
        by_port = node.bus != BLUETOOTH and bool(node.phys) and node.phys in physes
        if node.vendor == VALVE or by_uniq or by_port:
            out.append(node)
    return out


@dataclass
class Hush:
    """The grabs, held for as long as this object is.

    `refresh` reads the device list again and holds whatever is a
    controller's now, letting go of what no longer exists; the picker calls
    it when SDL announces a device arriving or leaving. A node that cannot
    be opened, or is grabbed already -- Steam holds the Deck's own -- is
    skipped and said once.
    """

    held: dict[str, int] = field(default_factory=dict)
    refused: set[str] = field(default_factory=set)

    def refresh(self, text: str | None = None) -> list[str]:
        """Hold every controller keyboard and mouse. Returns what is held."""
        if text is None:
            try:
                with open(DEVICES) as devices:
                    text = devices.read()
            except OSError:
                return sorted(self.held)
        wanted = {node.event: node for node in a_controllers(parse(text)) if node.event}
        for event in list(self.held):
            if event not in wanted:
                self._release(event)
        for event, node in wanted.items():
            if event in self.held or event in self.refused:
                continue
            path = f"/dev/input/{event}"
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                self._refuse(event, f"{node.name}: {exc.strerror}")
                continue
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError as exc:
                os.close(fd)
                self._refuse(event, f"{node.name}: {exc.strerror}")
                continue
            self.held[event] = fd
            trace.say("held", node=event, name=node.name)
        return sorted(self.held)

    def _refuse(self, event: str, why: str) -> None:
        if event not in self.refused:
            self.refused.add(event)
            trace.say("not-held", node=event, why=why)
            print(f"gotg-ui: could not hold {event} ({why}); it may still move the cursor", file=sys.stderr)

    def _release(self, event: str) -> None:
        fd = self.held.pop(event, None)
        if fd is None:
            return
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 0)
        except OSError:
            pass
        os.close(fd)

    def release(self) -> None:
        """Let every node go. The picker is leaving; the game is next."""
        for event in list(self.held):
            self._release(event)
