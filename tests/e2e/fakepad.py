"""A controller that is not there, made out of /dev/uinput.

The QA harness has one of these already (src/client/qa/pad.py), built on
python-evdev because the image it runs in has it. This one is ctypes and the
raw ioctls instead, so the controller tests need nothing but a kernel: they
have to run in a dev shell, on a Steam Deck over ssh, and anywhere somebody is
trying to work out why their pad does nothing.

The identity matters more than it looks. A pad wearing Microsoft's 045e:028e
is the case that broke the picker: danstick's clone mirrors its source's vendor
and product, so SDL finds that pair in its own database and calls *both* of
them "Xbox 360 Controller". Any test that only ever makes up its own ids would
have passed through the whole bug.
"""

from __future__ import annotations

import fcntl
import os
import struct
import time

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0

# The face buttons, by the kernel's names. danstick counts anything at or above
# BTN_FIRST (0x100) as a button somebody could claim a seat with.
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_SELECT, BTN_START = 0x136, 0x137, 0x13A, 0x13B
ABS_X, ABS_Y = 0x00, 0x01

UINPUT = "/dev/uinput"


def _iow(nr: int, size: int) -> int:
    """_IOW('U', nr, size), which is how uinput's ioctls are numbered."""
    return (1 << 30) | (size << 16) | (0x55 << 8) | nr


UI_DEV_CREATE = (0x55 << 8) | 1
UI_DEV_DESTROY = (0x55 << 8) | 2
UI_DEV_SETUP = _iow(3, 92)      # struct uinput_setup
UI_ABS_SETUP = _iow(4, 28)      # struct uinput_abs_setup
UI_SET_EVBIT = _iow(100, 4)
UI_SET_KEYBIT = _iow(101, 4)
UI_SET_RELBIT = _iow(102, 4)
UI_SET_ABSBIT = _iow(103, 4)
UI_SET_PHYS = _iow(108, 8)   # _IOW('U', 108, char*): sized as the pointer, which is the argument

KEY_ENTER, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = 28, 103, 108, 105, 106
REL_X, REL_Y = 0x00, 0x01

# What danstick and SDL both look for before calling something a joypad: buttons
# in the gamepad range, and a pair of absolute axes.
BUTTONS = (BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST, BTN_TL, BTN_TR, BTN_SELECT, BTN_START)


def available() -> str:
    """Why a pad cannot be made here, or "" when one can."""
    if not os.path.exists(UINPUT):
        return "no /dev/uinput on this machine"
    if not os.access(UINPUT, os.W_OK):
        return "/dev/uinput is not writable -- the uinput group, or a udev rule"
    return ""


class FakePad:
    """One controller, until it is closed."""

    def __init__(
        self,
        name: str,
        vendor: int = 0x045E,
        product: int = 0x028E,
        version: int = 0x0110,
        phys: str = "",
        keyboard: bool = False,
    ):
        """A joypad, or with `keyboard` a keyboard-and-mouse that says it is
        one -- the shape a Steam Controller's lizard mode and a Bluetooth
        Xbox pad's extra collections take. `phys` is what ties the two
        together in /proc/bus/input/devices, as the radio address does for
        the real thing."""
        self.name = name
        self._fd: int = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        if keyboard:
            for bit in (EV_KEY, 0x02):          # EV_REL
                fcntl.ioctl(self._fd, UI_SET_EVBIT, bit)
            for key in (KEY_ENTER, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, 0x110):  # + BTN_LEFT
                fcntl.ioctl(self._fd, UI_SET_KEYBIT, key)
            for axis in (REL_X, REL_Y):
                fcntl.ioctl(self._fd, UI_SET_RELBIT, axis)
        else:
            for bit in (EV_KEY, EV_ABS):
                fcntl.ioctl(self._fd, UI_SET_EVBIT, bit)
            for button in BUTTONS:
                fcntl.ioctl(self._fd, UI_SET_KEYBIT, button)
            for axis in (ABS_X, ABS_Y):
                fcntl.ioctl(self._fd, UI_SET_ABSBIT, axis)
                # struct uinput_abs_setup: __u16 code, then input_absinfo's six ints
                fcntl.ioctl(
                    self._fd,
                    UI_ABS_SETUP,
                    struct.pack("HHiiiiii", axis, 0, 0, -32768, 32767, 0, 0, 0),
                )
        # struct uinput_setup: input_id (bus, vendor, product, version), name, ff
        fcntl.ioctl(
            self._fd,
            UI_DEV_SETUP,
            struct.pack("HHHH80sI", 3, vendor, product, version, name.encode(), 0),
        )
        if phys:
            fcntl.ioctl(self._fd, UI_SET_PHYS, phys.encode() + b"\0")
        fcntl.ioctl(self._fd, UI_DEV_CREATE)
        # udev has to see it, danstick has to scan for it, and SDL has to be told
        # about it. A tenth of a second is not enough on a loaded machine.
        time.sleep(0.5)

    def _write(self, kind: int, code: int, value: int) -> None:
        os.write(self._fd, struct.pack("llHHi", 0, 0, kind, code, value))
        os.write(self._fd, struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0))

    def axis(self, code: int, value: int) -> None:
        """One absolute axis, moved. The stick, for the tests that time it."""
        self._write(EV_ABS, code, value)

    def down(self, button: int = BTN_SOUTH) -> None:
        self._write(EV_KEY, button, 1)

    def up(self, button: int = BTN_SOUTH) -> None:
        self._write(EV_KEY, button, 0)

    def tap(self, button: int = BTN_SOUTH, hold: float = 0.05) -> None:
        self.down(button)
        time.sleep(hold)
        self.up(button)

    def hold(self, button: int = BTN_SOUTH, seconds: float = 0.6) -> None:
        """Long enough to claim a seat. danstick's HOLD_SECONDS is 0.25."""
        self.down(button)
        time.sleep(seconds)
        self.up(button)

    def close(self) -> None:
        if self._fd >= 0:
            try:
                fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            finally:
                os.close(self._fd)
                self._fd = -1

    def __enter__(self) -> FakePad:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def event_node(name: str) -> str | None:
    """The /dev/input/eventN behind a device name, or None."""
    block_name, handlers = None, ""
    with open("/proc/bus/input/devices") as devices:
        for line in devices:
            if line.startswith('N: Name="'):
                block_name = line.split('"')[1]
            elif line.startswith("H: Handlers=") and block_name == name:
                handlers = line.split("=", 1)[1]
                for handler in handlers.split():
                    if handler.startswith("event"):
                        return f"/dev/input/{handler}"
    return None


def kernel_names() -> list[str]:
    """Every input device the kernel has, by name. For seeing a clone appear."""
    out = []
    with open("/proc/bus/input/devices") as devices:
        for line in devices:
            if line.startswith('N: Name="'):
                out.append(line.split('"')[1])
    return out
