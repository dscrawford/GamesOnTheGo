"""The loader's model half: asking whether a game is ready, and running the
install that makes it so.

The stub gotg here is the protocol's other end — argv shape, exit codes, the
dialog-suppression contract — so these hold the seam the client's own bats
tests hold from their side.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time

import pytest

from gotg_ui import prepare
from gotg_ui.catalog import Game
from gotg_ui.prepare import Preparer, is_ready


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


def stub(tmp_path, body: str) -> str:
    path = tmp_path / "gotg"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def bin_env(tmp_path, monkeypatch):
    def point_at(body: str) -> str:
        path = stub(tmp_path, body)
        monkeypatch.setenv("GOTG_BIN", path)
        return path

    return point_at


# --- is_ready -----------------------------------------------------------------


def test_ready_asks_the_client_with_the_qualified_id(bin_env, tmp_path):
    bin_env(f'echo "$@" > {tmp_path}/argv; echo ready')
    assert is_ready(game()) is True
    assert (tmp_path / "argv").read_text().split() == ["complete", "ready", "n64/usa.zelda"]


def test_exit_0_without_the_token_is_not_ready(bin_env):
    # The fail-open case: an older client's `complete` answers any unknown
    # subcommand with exit 0 and no output. Trusting the code alone would exec
    # into a build with no screen — the word is required as well.
    bin_env("exit 0")
    assert is_ready(game()) is False


def test_a_nonzero_answer_means_prepare(bin_env):
    bin_env("exit 1")
    assert is_ready(game()) is False


def test_a_missing_client_means_prepare_not_a_crash(monkeypatch):
    # Erring toward "ready" would exec into a build with no screen — the
    # failure this module exists to end — so every failure to ask is a no.
    monkeypatch.setenv("GOTG_BIN", "/nonexistent/gotg")
    assert is_ready(game()) is False


# --- Preparer -----------------------------------------------------------------


def wait_done(preparer, seconds=10):
    deadline = time.monotonic() + seconds
    while preparer.running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not preparer.running, "the stub install never finished"


def test_install_runs_with_dialogs_suppressed_and_both_streams_kept(bin_env, tmp_path):
    bin_env(
        f'echo "$@" > {tmp_path}/argv; echo "dialog=$GOTG_NO_DIALOG" >> {tmp_path}/argv\n'
        'echo "out line"; echo "err line" >&2; exit 0'
    )
    p = Preparer(game())
    wait_done(p)
    assert p.ok is True
    argv = (tmp_path / "argv").read_text()
    assert "install n64/usa.zelda" in argv
    # The child must not raise a zenity beside the loader that is already
    # showing its output — and the loader wants both streams, in one order.
    assert "dialog=1" in argv
    assert "out line" in p.tail()
    assert "err line" in p.tail()


def test_a_failed_install_reports_failure_and_keeps_the_reason(bin_env):
    bin_env('echo "error: no such game"; exit 3')
    p = Preparer(game())
    wait_done(p)
    assert p.ok is False
    assert "error: no such game" in p.tail()


def test_tail_keeps_the_end_of_a_long_build(bin_env):
    bin_env("i=0; while [ $i -lt 500 ]; do echo line-$i; i=$((i+1)); done")
    p = Preparer(game())
    wait_done(p)
    assert p.tail(3) == ["line-497", "line-498", "line-499"]


def test_cancel_ends_a_running_install(bin_env):
    bin_env('trap "exit 143" TERM; echo started; sleep 30')
    p = Preparer(game())
    deadline = time.monotonic() + 5
    while "started" not in p.tail() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert p.running
    started = time.monotonic()
    p.cancel()
    assert not p.running
    assert time.monotonic() - started < 6, "cancel must not sit out the sleep"
    assert p.ok is False


def test_the_frame_loop_calls_never_block(bin_env):
    bin_env("sleep 2")
    p = Preparer(game())
    started = time.monotonic()
    for _ in range(50):
        _ = p.running, p.tail()
    assert time.monotonic() - started < 0.5
    p.cancel()


def test_os_environ_is_not_mutated(bin_env):
    bin_env("exit 0")
    p = Preparer(game())
    wait_done(p)
    assert "GOTG_NO_DIALOG" not in os.environ


# --- is_ready: every way asking the client can fail ---------------------------


def _unreadable(tmp_path):
    path = stub(tmp_path, "echo ready")
    os.chmod(path, 0o644)
    return path


@pytest.mark.parametrize(
    "make_bin",
    [
        pytest.param(lambda t: stub(t, "exit 1"), id="the-client-says-no"),
        pytest.param(lambda t: "/nonexistent/gotg", id="nothing-on-path"),
        pytest.param(lambda t: str(t), id="a-directory-not-a-binary"),
        pytest.param(_unreadable, id="not-executable"),
    ],
)
def test_every_way_the_client_can_fail_to_answer_means_prepare(monkeypatch, tmp_path, make_bin):
    monkeypatch.setenv("GOTG_BIN", make_bin(tmp_path))
    assert is_ready(game()) is False


def test_a_hanging_client_means_prepare_too(monkeypatch):
    # Pinning the ceiling directly beats waiting it out for the same answer.
    def fake_run(*args, **kwargs):
        assert kwargs.get("timeout") == prepare.READY_TIMEOUT
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=prepare.READY_TIMEOUT)

    monkeypatch.setattr(prepare.subprocess, "run", fake_run)
    assert is_ready(game()) is False


# --- Preparer: further edges --------------------------------------------------


def test_a_missing_client_is_a_failed_prepare_not_a_crash(monkeypatch):
    # is_ready survives this exact case, so what runs next must too: the
    # loader's own failure screen, with the reason in the tail, rather than a
    # FileNotFoundError up through the event loop.
    monkeypatch.setenv("GOTG_BIN", "/nonexistent/gotg")
    p = Preparer(game())
    assert p.running is False
    assert p.ok is False
    assert any("/nonexistent/gotg" in line for line in p.tail())
    p.cancel()  # and cancelling the corpse is a no-op, not a crash


def test_an_install_that_prints_nothing_still_finishes_cleanly(bin_env):
    bin_env("exit 0")
    p = Preparer(game())
    wait_done(p)
    assert p.ok is True
    assert p.tail() == []


def test_cancel_after_the_process_already_finished_is_a_no_op(bin_env):
    bin_env("exit 0")
    p = Preparer(game())
    wait_done(p)
    p.cancel()
    assert p.ok is True, "cancel must not overwrite a real exit code"


def test_cancel_twice_does_not_raise(bin_env):
    bin_env('trap "exit 143" TERM; echo started; sleep 30')
    p = Preparer(game())
    deadline = time.monotonic() + 5
    while "started" not in p.tail() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert p.running
    p.cancel()
    p.cancel()
    assert not p.running


def test_cancel_stops_the_whole_group_not_just_the_wrapper(bin_env, tmp_path):
    # TERM to bash alone leaves its children running — an orphaned curl racing
    # the retry's resume corrupts the shared .part — so the group gets it.
    bin_env(f"sleep 30 &\necho $! > {tmp_path}/child\nwait")
    p = Preparer(game())
    deadline = time.monotonic() + 5
    while not (tmp_path / "child").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    child = int((tmp_path / "child").read_text())
    p.cancel()
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_invalid_utf8_on_stdout_does_not_crash_the_reader(bin_env):
    bin_env(r"printf 'bad: \xff\xfe end\n'; exit 0")
    p = Preparer(game())
    wait_done(p)
    assert p.ok is True
    assert any("bad:" in line for line in p.tail())


def test_tail_survives_being_polled_throughout_a_flood(bin_env):
    bin_env("i=0; while [ $i -lt 4000 ]; do echo line-$i; i=$((i+1)); done")
    p = Preparer(game())
    while p.running:
        p.tail(500)
    wait_done(p)
    assert p.tail(3) == ["line-3997", "line-3998", "line-3999"]


def test_uninstall_runs_through_the_same_loader_with_dialogs_suppressed(bin_env, tmp_path):
    bin_env(
        f'echo "$@" > {tmp_path}/argv; echo "dialog=$GOTG_NO_DIALOG" >> {tmp_path}/argv\n'
        'echo "uninstalled: A Game"; exit 0'
    )
    p = Preparer(game(), ["uninstall"])
    for _ in range(100):
        if not p.running:
            break
        time.sleep(0.05)
    assert p.ok is True
    argv = (tmp_path / "argv").read_text()
    assert "uninstall n64/usa.zelda" in argv
    assert "dialog=1" in argv
    assert "uninstalled: A Game" in p.tail()


def test_a_variant_is_asked_about_and_installed_by_name(bin_env, tmp_path):
    # A mod is its own environment: the one built for the plain game says
    # nothing about whether this one is ready, and installing it is its own run.
    log = tmp_path / "argv"
    bin_env(f'printf "%s\\n" "$*" >>{log}; echo ready')
    assert is_ready(game(), "bse")
    assert log.read_text().strip().endswith("n64/usa.zelda bse")

    p = Preparer(game(), None, "bse")
    wait_done(p)
    assert log.read_text().strip().splitlines()[-1] == "install n64/usa.zelda bse"
