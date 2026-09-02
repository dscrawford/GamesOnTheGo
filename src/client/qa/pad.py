#!/usr/bin/env python3
"""A virtual Xbox 360 pad that plays badly on purpose.

Clones the identity of the wired Microsoft pad, because that is the one GUID
every SDL build maps out of the box — the emulator under test must bind it
with no per-machine configuration. Created before the emulator starts, so SDL
finds it by scanning /dev/input and container/udev hotplug quirks never come
into play.

The input script is deliberately dumb: wait out the boot, mash START to get
past a title screen, then wiggle the stick and tap A until told to stop. The
point is not to play the game — it is to move pixels, so a recording that
stays frozen through this window means the inputs never reached the game.
"""

import argparse
import pathlib
import signal
import sys
import time

from evdev import AbsInfo, UInput
from evdev import ecodes as e

CAPABILITIES = {
    e.EV_KEY: [
        e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST,
        e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START, e.BTN_MODE,
        e.BTN_THUMBL, e.BTN_THUMBR,
    ],
    e.EV_ABS: [
        (e.ABS_X, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_Y, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_RX, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_RY, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_Z, AbsInfo(0, 0, 255, 0, 0, 0)),
        (e.ABS_RZ, AbsInfo(0, 0, 255, 0, 0, 0)),
        (e.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)),
        (e.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0)),
    ],
}

running = True


def stop(_sig, _frame):
    global running
    running = False


def press(ui, button, hold=0.15):
    ui.write(e.EV_KEY, button, 1)
    ui.syn()
    time.sleep(hold)
    ui.write(e.EV_KEY, button, 0)
    ui.syn()


def stick(ui, x, y, hold=0.4):
    ui.write(e.EV_ABS, e.ABS_X, x)
    ui.write(e.EV_ABS, e.ABS_Y, y)
    ui.syn()
    time.sleep(hold)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ready-file", required=True)
    ap.add_argument("--boot-wait", type=float, default=15.0)
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    ui = UInput(
        CAPABILITIES,
        name="Microsoft X-Box 360 pad",
        vendor=0x045E,
        product=0x028E,
        version=0x110,
    )
    print(f"pad: created {ui.device.path}", flush=True)
    pathlib.Path(args.ready_file).touch()

    deadline = time.monotonic() + args.boot_wait
    while running and time.monotonic() < deadline:
        time.sleep(0.2)

    for _ in range(3):
        if not running:
            break
        press(ui, e.BTN_START)
        time.sleep(1.5)

    full = 32767
    while running:
        for x, y in [(-full, 0), (full, 0), (0, -full), (0, full), (0, 0)]:
            if not running:
                break
            stick(ui, x, y)
        if running:
            press(ui, e.BTN_SOUTH)
            time.sleep(0.3)

    stick(ui, 0, 0, hold=0)
    ui.close()
    print("pad: closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
