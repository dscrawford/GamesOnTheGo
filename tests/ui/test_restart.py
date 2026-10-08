"""The restart into a newer picker: the command, the environment, and what
the exec does when it cannot."""

from __future__ import annotations

import pytest

from gotg_ui.launch import LaunchError
from gotg_ui.restart import Restart, command, environment, exec_restart


def test_the_command_is_the_new_roots_picker_with_this_ones_arguments():
    assert command("/nix/store/x-gotg-ui", ["--installed", "--platform", "n64"]) == [
        "/nix/store/x-gotg-ui/bin/gotg-ui",
        "--installed",
        "--platform",
        "n64",
    ]


def test_the_environment_keeps_the_daemon_handoff_and_does_not_touch_the_original():
    env = {"DANSTICK_FOLLOW": "4242", "DANSTICK_SKIP_DAEMON_CHECK": "1", "GOTG_UI_FULLSCREEN": "1", "HOME": "/h"}
    out = environment(env)
    assert out == env
    assert out is not env
    out["X"] = "y"
    assert "X" not in env


def test_an_exec_that_cannot_happen_is_a_launch_error_not_a_crash(tmp_path):
    with pytest.raises(LaunchError):
        exec_restart(Restart(str(tmp_path / "no-such-root")), argv=[])
