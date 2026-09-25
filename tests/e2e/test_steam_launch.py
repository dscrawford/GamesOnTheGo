"""The picker started by Steam hears danstick's clones.

On a Deck in Game Mode a Steam Controller paired -- danstick seated it and
published its clone, the picker drew the seat -- and then nothing pressed moved
anything. Steam hands everything it launches SDL_GAMECONTROLLER_IGNORE_DEVICES:
on that Deck, 2026-09-24, some seven hundred vendor/product pairs, Valve's own
and the Xbox 360 pad's among them. danstick's clones wear their source pad's ids
(`mirror` identity), so the picker's SDL skipped the one device it was there to
listen to. Read off the running picker's /proc/<pid>/environ; the pairs below
are the part of that list these clones wear.

Each case is a subprocess, because SDL reads the hint once, when its
controller subsystem starts: the picker's own Python, pygame's SDL 2, with the
environment Steam gives it. Without `import gotg_ui` first the clone is absent
-- the failure, reproduced -- and with it the clone is there.
"""

from __future__ import annotations

import os
import subprocess
import sys

import fakepad
import pytest
from fakepad import FakePad

# From the Deck's picker, verbatim but for the ~700 pairs between these.
STEAM_IGNORES = ",".join(
    [
        "0x2808/0x1015",
        "0x28de/0x1002",
        "0x28de/0x1205",
        "0x28de/0x1302",
        "0x28de/0x1303",
        "0x28de/0x1304",
        "0x28de/0x1305",
        "0x045e/0x028e",
        "0x045e/0x028f",
        "0x057e/0x2009",
        "0x054c/0x0ce6",
    ]
)

# What danstick published on the Deck for the Steam Controller: its name, the
# puck's ids, version 1 (/proc/bus/input/devices). And the commonest clone of
# all, an Xbox 360 pad's.
CLONES = {
    "steam-controller": (0x28DE, 0x1304, 0x0001),
    "xbox-360": (0x045E, 0x028E, 0x0110),
}

PROBE = """
import sys, time
if sys.argv[1] == "picker":
    import gotg_ui  # noqa: F401 -- the import is the fix under test
import pygame
pygame.init()
pygame.joystick.init()
want = sys.argv[2]
seen = []
end = time.monotonic() + 3.0
while time.monotonic() < end:
    pygame.event.pump()
    seen = [pygame.joystick.Joystick(i).get_guid() for i in range(pygame.joystick.get_count())]
    if any(want in guid for guid in seen):
        break
    time.sleep(0.1)
print(" ".join(seen))
"""


def _guid_part(vendor: int, product: int) -> str:
    """How a vendor/product pair reads inside an SDL GUID: little-endian
    words, vendor at bytes 4-5 and product at 8-9."""
    return f"{vendor & 0xFF:02x}{vendor >> 8:02x}0000{product & 0xFF:02x}{product >> 8:02x}"


def _sdl_sees(how: str, want: str) -> list[str]:
    env = {
        **os.environ,
        "SDL_VIDEODRIVER": "dummy",
        "PYGAME_HIDE_SUPPORT_PROMPT": "1",
        "SDL_GAMECONTROLLER_IGNORE_DEVICES": STEAM_IGNORES,
        "SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD": "1",
        # As the picker has always set it: HIDAPI off, so the evdev clone of a
        # Valve pad is SDL's to list at all.
        "SDL_JOYSTICK_HIDAPI": "0",
    }
    done = subprocess.run(
        [sys.executable, "-c", PROBE, how, want], env=env, capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


@pytest.mark.parametrize("clone", sorted(CLONES))
def test_the_picker_started_by_steam_sees_danstick_s_clone(clone):
    trouble = fakepad.available()
    if trouble:
        if os.environ.get("GOTG_E2E_REQUIRE") == "1":
            pytest.fail(f"controller e2e cannot run here, and GOTG_E2E_REQUIRE=1: {trouble}")
        pytest.skip(trouble)
    pytest.importorskip("pygame", reason="run this through `nix run .#test-controllers`")

    vendor, product, version = CLONES[clone]
    want = _guid_part(vendor, product)
    with FakePad("danstick Player 1", vendor=vendor, product=product, version=version):
        bare = _sdl_sees("bare", want)
        assert not any(want in guid for guid in bare), (
            f"Steam's list did not hide the clone, so this does not reproduce the Deck: {bare}"
        )
        picker = _sdl_sees("picker", want)
        assert any(want in guid for guid in picker), (
            f"the picker, started as Steam starts it, still cannot see danstick's clone: {picker}"
        )
