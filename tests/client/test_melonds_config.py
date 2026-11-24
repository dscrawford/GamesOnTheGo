"""melonds-config.py: dotted keys into melonDS.toml, everything else kept."""

from __future__ import annotations

import json
import os
import sys

import pytest

pytest.importorskip("tomlkit")
import tomllib  # noqa: E402

sys.path.insert(0, os.getcwd())
import melonds_config  # noqa: E402

WRITTEN = """\
TargetFPS = 60.0

[Instance0]
JoystickID = 0
SaveFilePath = ""

[Instance0.Joystick]
A = -1
HK_Pause = 7

[Instance0.Window0]
ScreenLayout = 0
Geometry = "AdnQyw"
"""


def read(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_set_keys_change_and_the_rest_stay(tmp_path):
    path = tmp_path / "melonDS.toml"
    path.write_text(WRITTEN)
    melonds_config.main(str(path), json.dumps({"Instance0.Joystick.A": 1, "Instance0.Window0.ScreenLayout": 2}))
    doc = read(path)
    assert doc["Instance0"]["Joystick"]["A"] == 1
    assert doc["Instance0"]["Window0"]["ScreenLayout"] == 2
    assert doc["Instance0"]["Joystick"]["HK_Pause"] == 7
    assert doc["Instance0"]["Window0"]["Geometry"] == "AdnQyw"
    assert doc["TargetFPS"] == 60.0


def test_a_first_launch_with_no_file_makes_one(tmp_path):
    path = tmp_path / "melonDS" / "melonDS.toml"
    melonds_config.main(str(path), json.dumps({"Emu.ExternalBIOSEnable": False, "DS.BIOS7Path": "/s/bios7.bin"}))
    doc = read(path)
    assert doc["Emu"]["ExternalBIOSEnable"] is False
    assert doc["DS"]["BIOS7Path"] == "/s/bios7.bin"


def test_the_pad_file_applies_after_the_pinned_settings(tmp_path):
    path = tmp_path / "melonDS.toml"
    pads = tmp_path / "melonds-pad.json"
    pads.write_text(json.dumps({"Instance0.JoystickID": 2}))
    melonds_config.main(str(path), json.dumps({"Instance0.JoystickID": 0}), f"@{pads}")
    assert read(path)["Instance0"]["JoystickID"] == 2


def test_a_missing_pad_file_is_no_pad_not_no_launch(tmp_path):
    path = tmp_path / "melonDS.toml"
    melonds_config.main(str(path), json.dumps({"Mouse.Hide": True}), f"@{tmp_path}/absent.json")
    assert read(path)["Mouse"]["Hide"] is True


def test_a_file_melonds_could_not_read_either_is_started_over(tmp_path):
    path = tmp_path / "melonDS.toml"
    path.write_text("[Instance0\nbroken")
    melonds_config.main(str(path), json.dumps({"Instance0.JoystickID": 1}))
    assert read(path)["Instance0"]["JoystickID"] == 1
