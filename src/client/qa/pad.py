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

With --chord-file, both shoulders and Start are held together for
--chord-seconds once that file appears: the kill switch's chord, for a run
grading the overlay's exit ring. Held for less than the kill switch's hold,
so the ring is drawn and nothing is stopped. Nothing else here presses a
shoulder, so the chord cannot happen by accident.
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
chord = None  # (path, seconds) until the chord has been held, then None


def stop(_sig, _frame):
    global running
    running = False


def pause(ui, seconds):
    """Sleep, in slices short enough that the chord starts within a frame or
    so of its file appearing, whatever the script was in the middle of."""
    global chord
    until = time.monotonic() + seconds
    while running:
        if chord and pathlib.Path(chord[0]).exists():
            held = chord[1]
            chord = None
            buttons = (e.BTN_TL, e.BTN_TR, e.BTN_START)
            for button in buttons:
                ui.write(e.EV_KEY, button, 1)
            ui.syn()
            print(f"pad: chord held for {held}s", flush=True)
            time.sleep(held)
            for button in buttons:
                ui.write(e.EV_KEY, button, 0)
            ui.syn()
        left = until - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.05))


def press(ui, button, hold=0.15):
    ui.write(e.EV_KEY, button, 1)
    ui.syn()
    pause(ui, hold)
    ui.write(e.EV_KEY, button, 0)
    ui.syn()


def stick(ui, x, y, hold=0.4):
    ui.write(e.EV_ABS, e.ABS_X, x)
    ui.write(e.EV_ABS, e.ABS_Y, y)
    ui.syn()
    pause(ui, hold)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ready-file", required=True)
    ap.add_argument("--boot-wait", type=float, default=15.0)
    ap.add_argument("--chord-file")
    ap.add_argument("--chord-seconds", type=float, default=3.0)
    args = ap.parse_args()
    global chord
    if args.chord_file:
        chord = (args.chord_file, args.chord_seconds)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    ui = UInput(
        CAPABILITIES,
        name="Microsoft X-Box 360 pad",
        vendor=0x045E,
        product=0x028E,
        version=0x110,
    )
    # ui.device is evdev looking its own creation back up in /dev/input, which
    # needs udev and so comes back None in a container. The pad is real either
    # way — SDL finds it by scanning /dev/input — so this is only a label.
    where = getattr(ui.device, "path", None) or ui.devnode or "(no udev)"
    print(f"pad: created {where}", flush=True)
    pathlib.Path(args.ready_file).touch()

    pause(ui, args.boot_wait)

    for _ in range(3):
        if not running:
            break
        press(ui, e.BTN_START)
        pause(ui, 1.5)

    # A before anything moves the selection. The recomp launchers open on
    # their own "Start game" item, and the loop below moves the stick four
    # times before it first presses anything -- so it walked down the menu
    # and pressed A on Exit, which looked like the game quitting on its own
    # a few seconds after the inputs began.
    for _ in range(3):
        if not running:
            break
        press(ui, e.BTN_SOUTH)
        pause(ui, 1.0)

    full = 32767
    while running:
        for x, y in [(-full, 0), (full, 0), (0, -full), (0, full), (0, 0)]:
            if not running:
                break
            stick(ui, x, y)
        if running:
            press(ui, e.BTN_SOUTH)
            pause(ui, 0.3)

    stick(ui, 0, 0, hold=0)
    ui.close()
    print("pad: closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
