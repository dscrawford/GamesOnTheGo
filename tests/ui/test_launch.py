"""Handing off to the client.

What matters most: the id is qualified. `usa.bugs_life` is on two platforms and
a bare id would be ambiguous — `gotg play` would refuse it, or worse, pick.

The exec itself is checked by running one, because a call that replaces the
process cannot be observed from inside it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gotg_ui.catalog import Game
from gotg_ui.launch import LaunchError, command_for, play

SRC_UI = Path(__file__).resolve().parents[2] / "src" / "ui"


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


def test_the_id_is_always_qualified_by_platform():
    # Ids are unique per platform and not globally — usa.bugs_life is on both
    # n64 and snes — so the grid, which knows which one you were looking at,
    # must say. A bare id throws that away.
    assert command_for(game()) == ["gotg", "play", "n64/usa.zelda"]
    assert command_for(game(platform="snes")) == ["gotg", "play", "snes/usa.zelda"]


def test_the_binary_can_be_pointed_somewhere_else(monkeypatch):
    monkeypatch.setenv("GOTG_BIN", "/opt/gotg/bin/gotg")
    assert command_for(game())[0] == "/opt/gotg/bin/gotg"


def test_an_empty_override_falls_back_rather_than_execing_nothing(monkeypatch):
    monkeypatch.setenv("GOTG_BIN", "")
    assert command_for(game())[0] == "gotg"


@pytest.mark.parametrize(
    ("platform", "game_id"),
    [("n64", "usa.zelda"), ("gb", "eur.b_c_kid_2"), ("gamecube", "usa.super_mario_sunshine")],
)
def test_the_qualified_form_is_what_the_client_documents(platform, game_id):
    # `gotg play snes/usa.bugs_life` — platform, slash, id.
    assert command_for(game(game_id, platform))[2] == f"{platform}/{game_id}"


def test_a_missing_client_is_an_error_that_names_it(monkeypatch):
    # execvp raises before it replaces anything, so this is reachable — and it
    # is the one failure a person will actually hit, on a machine where the UI
    # was installed and the client was not.
    monkeypatch.setenv("GOTG_BIN", "/nonexistent/gotg-does-not-exist")
    with pytest.raises(LaunchError, match="gotg-does-not-exist"):
        play(game())


def test_play_really_execs(tmp_path):
    """The process is replaced, so this proves it by watching one be.

    A child python execs `play`, pointed at a stand-in for the client; what
    lands on stdout is that stand-in's argv, from a process that no longer has
    a python in it.
    """
    marker = tmp_path / "argv.txt"
    stand_in = tmp_path / "fake-gotg"
    stand_in.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {marker}\necho replaced\n')
    stand_in.chmod(0o755)

    script = (
        f"import sys; sys.path.insert(0, {str(SRC_UI)!r})\n"
        "from gotg_ui.catalog import Game\n"
        "from gotg_ui.launch import play\n"
        "play(Game(id='usa.zelda', platform='n64', title='t', handler='single_file'))\n"
        "raise SystemExit('exec did not replace this process')\n"
    )

    done = subprocess.run(
        [sys.executable, "-c", script],
        env={"GOTG_BIN": str(stand_in), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "replaced"
    assert marker.read_text().split() == ["play", "n64/usa.zelda"]


def test_the_grid_never_needs_the_client_to_guess():
    """222 ids in the real library are on more than one platform.

    eur.asterix is on gb, nes and snes. The grid knows which tile the cursor
    was on, so it says — and the client is never asked to pick between three
    games that share a name.
    """
    on_three = [command_for(game("eur.asterix", p)) for p in ("gb", "nes", "snes")]
    assert len({tuple(c) for c in on_three}) == 3, "three platforms, three different commands"
    assert [c[2] for c in on_three] == ["gb/eur.asterix", "nes/eur.asterix", "snes/eur.asterix"]
