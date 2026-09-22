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

from gotg_ui.bindings import describe_all, pad_controls

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


@pytest.mark.parametrize(
    "padmap_id,expected",
    [
        ("dpup", "Up"),
        ("a", "A"),
        ("x", "B"),                 # the N64's B is the pad's X
        ("leftshoulder", "L"),
        ("lefttrigger", "Z"),
        ("rightstick_up", "C-Up"),  # padmap's spelling of righty-
        ("leftstick_left", "X-Axis/Lo"),
        ("b", None),                # drives nothing on an N64 pad
    ],
)
def test_a_press_is_named_by_what_the_console_calls_it(padmap_id, expected, monkeypatch):
    """The bug the dots were invisible for.

    padmap's profile answers `leftshoulder`; every label on the drawing is
    called `L`. Nothing compared the two, so a press lit nothing at all.
    """
    monkeypatch.setenv("GOTG_DATA", str(pathlib.Path(__file__).parents[2] / "src" / "client" / "data"))
    assert pad_controls("Nintendo64").get(padmap_id) == expected


def test_a_console_the_table_has_never_heard_of_translates_nothing(monkeypatch):
    # Dolphin's two publish no console, and there the drawing is labelled with
    # padmap's own ids already -- so an empty table is the right answer, and
    # the door falls back to passing the id through.
    monkeypatch.setenv("GOTG_DATA", str(pathlib.Path(__file__).parents[2] / "src" / "client" / "data"))
    assert pad_controls("GameCube") == {}
    assert pad_controls(None) == {}
