"""The shipped ares binding table, read the way the gate's screen reads it.

`src/client/data/ares-pads.json` is one file for two consumers: `gotg pads`
writes settings.bml from it, and `bindings.py` describes it on the launch
gate. So a binding that reads wrong on that screen is wrong in the emulator,
and these pin the one that was.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from gotg_ui.bindings import describe_all

TABLE = json.loads((pathlib.Path(__file__).parents[2] / "src" / "client" / "data" / "ares-pads.json").read_text())
N64 = TABLE["Nintendo64"]["buttons"]


def test_the_n64_stick_is_the_stick_and_the_dpad_is_the_dpad():
    """L-Up on the gate read "D-pad up", under "Up — D-pad up".

    ares' N64 Gamepad has L-Up/L-Down/L-Left/L-Right -- the analog stick's
    digital corners, for a pad with no stick -- beside the real X-Axis and
    Y-Axis. Bound to the D-pad, one press sent a D-pad direction *and* shoved
    the stick to full deflection. They stay unbound: the stick is analog
    through the axes, and the D-pad is only a D-pad.
    """
    assert "L-Up" not in N64
    assert not [name for name in N64 if name.startswith("L-")], "the stick's digital corners are bound again"
    assert N64["Up"] == "dpup"
    assert N64["X-Axis/Lo"] == "leftx-"
    assert N64["Y-Axis/Hi"] == "lefty+"


@pytest.mark.parametrize(
    "control,expected",
    [
        ("Up", "D-pad up"),
        ("X-Axis/Lo", "Left stick left"),
        ("Y-Axis/Lo", "Left stick up"),
        ("C-Right", "Right stick right"),
        ("Z", "Left trigger"),
    ],
)
def test_the_gate_describes_an_n64_pad_in_words_somebody_can_check(control, expected):
    assert describe_all(N64[control]) == expected
