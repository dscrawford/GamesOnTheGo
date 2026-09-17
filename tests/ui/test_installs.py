"""Installs that run behind the grid.

A stub gotg plays the client, as in test_prepare: what is held here is that
an install started from the menu keeps the grid usable, reports where it is
for a ring on the tile, and lands as a badge when it finishes.
"""

from __future__ import annotations

import stat
import time

import pytest

from gotg_ui.catalog import Game
from gotg_ui.installs import Installs
from gotg_ui.prepare import Progress


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


@pytest.fixture
def bin_env(tmp_path, monkeypatch):
    def point_at(body: str) -> str:
        path = tmp_path / "gotg"
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("GOTG_BIN", str(path))
        return str(path)

    return point_at


def settle(installs: Installs, seconds=10):
    deadline = time.monotonic() + seconds
    done: list = []
    while time.monotonic() < deadline:
        done += installs.poll()
        if not installs.keys:
            return done
        time.sleep(0.02)
    raise AssertionError("the stub install never finished")


def test_an_install_runs_behind_the_grid_and_lands_as_done(bin_env, tmp_path):
    bin_env(f'echo "$@" > {tmp_path}/argv; exit 0')
    installs = Installs()
    installs.start(game(), variant="60fps")
    assert installs.running(game().key)
    assert settle(installs) == [game().key]
    assert not installs.running(game().key)
    assert "install n64/usa.zelda 60fps" in (tmp_path / "argv").read_text()


def test_progress_lines_become_the_ring(bin_env):
    bin_env('printf "progress\\t25\\t100\\t5\\tx\\n" >&2; sleep 0.3; exit 0')
    installs = Installs()
    installs.start(game())
    deadline = time.monotonic() + 5
    while installs.progress(game().key) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert installs.progress(game().key) == Progress(done=25, total=100, rate=5, what="x")
    assert installs.rings() == {game().key: (0.25, False)}
    settle(installs)


def test_a_build_with_no_figures_is_a_ring_with_no_fraction(bin_env):
    bin_env("sleep 0.3; exit 0")
    installs = Installs()
    installs.start(game())
    assert installs.rings() == {game().key: (None, False)}
    settle(installs)


def test_a_failed_install_is_remembered_with_its_last_words(bin_env):
    bin_env('echo "nix: build of /nix/store/x failed" >&2; exit 1')
    installs = Installs()
    installs.start(game())
    assert settle(installs) == []
    assert game().key in installs.failed
    assert "failed" in installs.failed[game().key]
    assert installs.rings() == {game().key: (1.0, True)}
    # Trying again clears the mark.
    installs.start(game())
    assert game().key not in installs.failed
    settle(installs)


def test_starting_twice_is_one_install(bin_env, tmp_path):
    bin_env(f'echo run >> {tmp_path}/runs; sleep 0.3; exit 0')
    installs = Installs()
    installs.start(game())
    installs.start(game())
    settle(installs)
    assert (tmp_path / "runs").read_text().count("run") == 1


def test_take_hands_a_running_install_to_the_foreground(bin_env):
    # Play on a game that is installing: the loader adopts the install
    # rather than starting a second one against the client's lock.
    bin_env("sleep 0.3; exit 0")
    installs = Installs()
    installs.start(game())
    taken = installs.take(game().key)
    assert taken is not None
    assert not installs.running(game().key)
    assert installs.take(game().key) is None
    deadline = time.monotonic() + 5
    while taken.running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert taken.ok


def test_cancel_ends_it_and_leaves_no_mark(bin_env):
    bin_env("sleep 30; exit 0")
    installs = Installs()
    installs.start(game())
    installs.cancel(game().key)
    assert not installs.running(game().key)
    assert installs.rings() == {}
    assert installs.poll() == []
