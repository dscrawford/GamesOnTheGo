#!/usr/bin/env python3
"""The kill switch against a real controller, a real watcher and a real kill.

The unit tests cover the combination on a clock they own, and the bats tests
cover the wiring. Neither of them can catch what needs real SDL, a real pad and
a real process tree: SDL not seeing the pad at all, a trigger's analog range
not crossing the threshold once SDL has rescaled it, a signal going somewhere
other than the game, or a game that ignores being asked to stop.

So this makes a virtual Xbox 360 pad with uinput — the same identity
qa/pad.py uses, because it is the one every SDL build maps out of the box —
holds the combination on it, and checks that the process the watcher was
pointed at is gone.

Needs /dev/uinput and python-evdev, so it skips rather than fails where those
are not available: a build sandbox has neither.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time

from evdev import AbsInfo, UInput
from evdev import ecodes as e

# Long enough for SDL's hotplug to notice a device appearing, short enough that
# the whole file is a few seconds. Both halves were measured, not guessed: SDL
# opens hidapi devices on a background thread.
SETTLE_S = 1.5

PAD = {
    "name": "Microsoft X-Box 360 pad",
    "vendor": 0x045E,
    "product": 0x028E,
    "version": 0x110,
}

CAPABILITIES = {
    e.EV_KEY: [
        e.BTN_SOUTH,
        e.BTN_EAST,
        e.BTN_NORTH,
        e.BTN_WEST,
        e.BTN_TL,
        e.BTN_TR,
        e.BTN_SELECT,
        e.BTN_START,
        e.BTN_MODE,
        e.BTN_THUMBL,
        e.BTN_THUMBR,
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

COMBO = (e.BTN_TL, e.BTN_TR, e.BTN_START)

# A trigger pulled all the way, in the 0-255 range this device advertises.
# Deliberately not written in SDL's own units: what is being checked is that
# SDL's rescaling of whatever range a pad declares still lands past the
# threshold the watcher compares against.
TRIGGER_FULL = 255


def hold(pad: UInput, down: bool) -> None:
    """Both shoulders and Start, which is what most pads report."""
    for code in COMBO:
        pad.write(e.EV_KEY, code, 1 if down else 0)
    pad.syn()


def hold_triggers(pad: UInput, down: bool) -> None:
    """The other half of the advertised combination: both analog triggers."""
    level = TRIGGER_FULL if down else 0
    pad.write(e.EV_ABS, e.ABS_Z, level)
    pad.write(e.EV_ABS, e.ABS_RZ, level)
    pad.write(e.EV_KEY, e.BTN_START, 1 if down else 0)
    pad.syn()


def hold_mixed(pad: UInput, down: bool) -> None:
    """One side a button, the other an axis — the asymmetry ks_input exists
    for, and the one no single-shaped test would catch."""
    pad.write(e.EV_KEY, e.BTN_TL, 1 if down else 0)
    pad.write(e.EV_ABS, e.ABS_RZ, TRIGGER_FULL if down else 0)
    pad.write(e.EV_KEY, e.BTN_START, 1 if down else 0)
    pad.syn()


def watcher_for(binary: str, pid: int, hold_ms: int = 1000, grace_ms: int = 5000) -> subprocess.Popen:
    return subprocess.Popen(
        [binary, "--pid", str(pid), "--hold-ms", str(hold_ms), "--grace-ms", str(grace_ms), "--poll-ms", "50"],
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(running: subprocess.Popen, timeout: float = 8.0) -> str:
    try:
        running.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        running.terminate()
    return running.stderr.read() if running.stderr else ""


def cleanup(game: subprocess.Popen) -> None:
    if game.poll() is None:
        os.killpg(game.pid, signal.SIGKILL)
        game.wait(timeout=5)


def report(case: str, ok: bool, noise: str = "", detail: str = "") -> bool:
    print(f"{'ok' if ok else 'not ok'} - {case}")
    if not ok:
        if detail:
            print(f"    {detail}")
        for line in noise.strip().splitlines():
            print(f"    | {line}")
    return ok


def one(
    watcher: str,
    case: str,
    *,
    pad_first: bool,
    hold_ms: int,
    press_ms: int,
    expect_dead: bool,
    combo=hold,
    grace_ms: int = 5000,
    stubborn: bool = False,
    expect_forced: bool = False,
) -> bool:
    pad = UInput(CAPABILITIES, **PAD) if pad_first else None
    if pad:
        time.sleep(SETTLE_S)

    # Its own session, like a game launched from Steam: the watcher signals the
    # process group, and this is what makes that path the one under test. A
    # game that ignores SIGTERM stands in for the hung emulator the whole
    # feature exists for — an ignored disposition survives exec, so the sleep
    # really does refuse to go.
    command = ["bash", "-c", 'trap "" TERM; exec sleep 120'] if stubborn else ["sleep", "120"]
    game = subprocess.Popen(command, start_new_session=True)
    running = watcher_for(watcher, game.pid, hold_ms, grace_ms)
    time.sleep(1.0)

    if pad is None:
        # Plugged in with the game already running, which is the case SDL gets
        # wrong if the watcher only enumerates at startup.
        pad = UInput(CAPABILITIES, **PAD)
        time.sleep(SETTLE_S)

    combo(pad, True)
    time.sleep(press_ms / 1000)
    combo(pad, False)

    noise = finish(running, timeout=max(8.0, grace_ms / 1000 + 5.0))
    dead = game.poll() is not None
    cleanup(game)
    pad.close()

    # Whether it had to be forced is read from what the watcher said rather
    # than from a stopwatch: the grace period starts when the hold completes,
    # which is before the combo is let go.
    forced = "killing" in noise
    ok = dead == expect_dead and forced == expect_forced
    return report(case, ok, noise, f"dead={dead} expected={expect_dead} forced={forced}")


def grandchild(watcher: str) -> bool:
    """A process the game started must go with it.

    The reason signal_game aims at a process group: an emulator run under a
    compositor or a shim leaves children, and a signal to one pid would leave
    them holding the screen.
    """
    pad = UInput(CAPABILITIES, **PAD)
    time.sleep(SETTLE_S)

    marker = f"/tmp/gotg-killswitch-child-{os.getpid()}"
    game = subprocess.Popen(["bash", "-c", f'sleep 120 & echo $! >"{marker}"; wait'], start_new_session=True)
    child = None
    for _ in range(50):
        if os.path.exists(marker):
            child = int(pathlib.Path(marker).read_text().strip())
            os.remove(marker)
            break
        time.sleep(0.1)

    running = watcher_for(watcher, game.pid)
    time.sleep(1.0)
    hold(pad, True)
    time.sleep(1.8)
    hold(pad, False)
    noise = finish(running)

    time.sleep(0.3)
    child_gone = child is not None
    if child is not None:
        try:
            os.kill(child, 0)
            child_gone = False
        except ProcessLookupError:
            child_gone = True
    dead = game.poll() is not None

    cleanup(game)
    if child is not None and not child_gone:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
    pad.close()

    return report(
        "a process the game started dies with it",
        dead and child_gone,
        noise,
        f"game_dead={dead} child_gone={child_gone} child={child}",
    )


def two_pads(watcher: str) -> bool:
    """The combination is held on one controller, not assembled from several.

    Each pad is timed on its own, so player one's shoulders and player two's
    Start are two people playing, not a kill switch. The second half of the
    case holds the real combination on one pad, which is what keeps a watcher
    that had quietly died from passing the first half for free.
    """
    first = UInput(CAPABILITIES, **PAD)
    second = UInput(CAPABILITIES, **PAD)
    time.sleep(SETTLE_S)

    game = subprocess.Popen(["sleep", "120"], start_new_session=True)
    running = watcher_for(watcher, game.pid)
    time.sleep(1.0)

    first.write(e.EV_KEY, e.BTN_TL, 1)
    first.write(e.EV_KEY, e.BTN_TR, 1)
    first.syn()
    second.write(e.EV_KEY, e.BTN_START, 1)
    second.syn()
    time.sleep(2.0)  # twice the hold, split between them the whole time
    survived = game.poll() is None
    first.write(e.EV_KEY, e.BTN_TL, 0)
    first.write(e.EV_KEY, e.BTN_TR, 0)
    first.syn()
    second.write(e.EV_KEY, e.BTN_START, 0)
    second.syn()

    hold(first, True)
    time.sleep(1.8)
    hold(first, False)
    noise = finish(running)
    dead = game.poll() is not None

    cleanup(game)
    first.close()
    second.close()

    return report(
        "two controllers splitting the combination never add up to it",
        survived and dead,
        noise,
        f"survived_the_split={survived} stopped_by_one_pad={dead}",
    )


def hostile_name(watcher: str) -> bool:
    """A controller names itself, and that name lands in the log somebody
    reads afterwards. It must arrive as text rather than as escape
    sequences."""
    # An identity SDL's mapping database does not know: for anything it does
    # know — the Xbox pad the other cases use — SDL answers with the database's
    # name and the device never gets to say anything at all.
    hostile = {"name": "pad\x1b[31;5mred\x07", "vendor": 0x1234, "product": 0x5678, "version": 0x111}
    pad = UInput(CAPABILITIES, **hostile)
    time.sleep(SETTLE_S)
    game = subprocess.Popen(["sleep", "10"], start_new_session=True)
    running = subprocess.Popen([watcher, "--pid", str(game.pid), "--poll-ms", "50"], stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    running.terminate()
    noise = running.stderr.read() if running.stderr else ""
    running.wait(timeout=5)
    os.killpg(game.pid, signal.SIGKILL)
    game.wait(timeout=5)
    pad.close()

    ok = "\x1b" not in noise and "\x07" not in noise and "pad?[31;5mred?" in noise
    print(f"{'ok' if ok else 'not ok'} - a controller cannot write escape sequences into the log")
    if not ok:
        print(f"    {noise.strip()!r}")
    return ok


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: killswitch_e2e.py <path to gotg-killswitch>", file=sys.stderr)
        return 2
    watcher = argv[1]

    cases = [
        ("a pad already connected stops the game", dict(pad_first=True, hold_ms=1000, press_ms=1800, expect_dead=True)),
        (
            "a pad plugged in mid-game stops it too",
            dict(pad_first=False, hold_ms=1000, press_ms=1800, expect_dead=True),
        ),
        ("letting go early leaves the game alone", dict(pad_first=True, hold_ms=3000, press_ms=900, expect_dead=False)),
        (
            "both triggers stop it too, once SDL has rescaled them",
            dict(pad_first=True, hold_ms=1000, press_ms=1800, expect_dead=True, combo=hold_triggers),
        ),
        (
            "one side a button and the other a trigger is still the combination",
            dict(pad_first=True, hold_ms=1000, press_ms=1800, expect_dead=True, combo=hold_mixed),
        ),
        (
            "a game that refuses to stop is stopped anyway, once its grace runs out",
            dict(
                pad_first=True,
                hold_ms=1000,
                press_ms=1800,
                expect_dead=True,
                stubborn=True,
                grace_ms=1500,
                expect_forced=True,
            ),
        ),
    ]
    passed = [one(watcher, case, **args) for case, args in cases]
    passed.append(grandchild(watcher))
    passed.append(two_pads(watcher))
    passed.append(hostile_name(watcher))
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
