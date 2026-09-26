"""The overlay beside the picker: started with the bar alone, stopped before
the hand-off, and nothing at all where there is no kill switch."""

from __future__ import annotations

import os
import stat
import subprocess
import sys

from gotg_ui import beside


def _fake(tmp_path, body: str) -> str:
    path = tmp_path / "gotg-killswitch"
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_it_is_the_bar_alone_beside_this_process(tmp_path, monkeypatch):
    said = tmp_path / "argv"
    fake = _fake(tmp_path, f"import sys; open({str(said)!r}, 'w').write(' '.join(sys.argv[1:]))")
    monkeypatch.setenv("GOTG_KILLSWITCH_BIN", fake)
    monkeypatch.delenv("GOTG_KILLSWITCH_OVERLAY", raising=False)
    process = beside.start(4242)
    assert process is not None
    process.wait(timeout=10)
    assert said.read_text() == "--pid 4242 --overlay-only --quiet"


def test_stop_takes_it_down_and_kills_one_that_will_not_go(tmp_path, monkeypatch):
    stubborn = _fake(tmp_path, "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)")
    monkeypatch.setattr(beside, "STOP_SECONDS", 0.2)
    process = subprocess.Popen([stubborn])
    beside.stop(process)
    assert process.poll() is not None
    beside.stop(process)  # twice is nothing
    beside.stop(None)


def test_no_kill_switch_or_no_bar_is_no_overlay(monkeypatch):
    monkeypatch.delenv("GOTG_KILLSWITCH_BIN", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert beside.start(os.getpid()) is None
    monkeypatch.setenv("GOTG_KILLSWITCH_BIN", "/bin/true")
    monkeypatch.setenv("GOTG_KILLSWITCH_OVERLAY", "0")
    assert beside.start(os.getpid()) is None
