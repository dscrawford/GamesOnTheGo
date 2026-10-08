"""The restart into a newer picker: the command, the client's word, and what
the exec does when it cannot."""

from __future__ import annotations

import pytest

from gotg_ui.launch import LaunchError
from gotg_ui.restart import Restart, command, exec_restart, said_root


def test_the_command_is_the_new_roots_picker_with_this_ones_arguments():
    assert command("/nix/store/x-gotg-ui", ["--installed", "--platform", "n64"]) == [
        "/nix/store/x-gotg-ui/bin/gotg-ui",
        "--installed",
        "--platform",
        "n64",
    ]


def test_the_clients_word_is_read_off_its_last_picker_line():
    lines = ["gotg: up to date", "picker\t/nix/store/a-gotg-ui", "gotg-ui: up to date", "picker\t/nix/store/b-gotg-ui "]
    assert said_root(lines) == "/nix/store/b-gotg-ui"


def test_an_older_client_that_says_nothing_names_no_root():
    assert said_root(["gotg: up to date"]) is None
    assert said_root([]) is None
    assert said_root(["picker\t"]) is None


def test_an_exec_that_cannot_happen_is_a_launch_error_not_a_crash(tmp_path):
    with pytest.raises(LaunchError):
        exec_restart(Restart(str(tmp_path / "no-such-root")), argv=[])
    # A NUL in the path is a ValueError from the exec, not an OSError.
    with pytest.raises(LaunchError):
        exec_restart(Restart(str(tmp_path) + "\0x"), argv=[])


def test_a_usable_root_is_a_store_path_with_the_picker_in_it(tmp_path, monkeypatch):
    from gotg_ui.restart import usable

    assert not usable(str(tmp_path))
    assert not usable("/nix/store/zzzz-gotg-ui")
    # realpath is what is checked: a store-looking path must really be one.
    fake = tmp_path / "nix" / "store" / "aaaa-gotg-ui" / "bin"
    fake.mkdir(parents=True)
    (fake / "gotg-ui").write_text("#!/bin/sh\n")
    (fake / "gotg-ui").chmod(0o755)
    monkeypatch.setattr("os.path.realpath", lambda p: "/nix/store/aaaa-gotg-ui" if "aaaa" in p else p)
    monkeypatch.setattr("os.access", lambda p, mode: p == "/nix/store/aaaa-gotg-ui/bin/gotg-ui")
    assert usable("/anywhere/aaaa-gotg-ui")
