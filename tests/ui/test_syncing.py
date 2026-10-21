"""Property under test: the picker syncs saves a minute after it is up and
every ten minutes after, never sooner, and never when told not to."""

from __future__ import annotations

from gotg_ui import syncing
from gotg_ui.syncing import EVERY_SECONDS, FIRST_SECONDS, Cadence


def test_the_first_sync_waits_for_the_grid_to_settle():
    cadence = Cadence.start(100.0)
    assert not cadence.due(100.0)
    assert not cadence.due(100.0 + FIRST_SECONDS - 0.1)
    assert cadence.due(100.0 + FIRST_SECONDS)


def test_after_a_sync_the_next_is_ten_minutes_on():
    cadence = Cadence.start(0.0).ran(500.0)
    assert cadence.next_at == 500.0 + EVERY_SECONDS
    assert not cadence.due(500.0 + EVERY_SECONDS - 1)
    assert cadence.due(500.0 + EVERY_SECONDS)


def test_a_cadence_is_a_value():
    before = Cadence.start(0.0)
    after = before.ran(10.0)
    assert before.next_at == FIRST_SECONDS and after is not before


def test_the_picker_can_be_told_not_to(monkeypatch):
    monkeypatch.delenv("GOTG_UI_SAVES_SYNC", raising=False)
    assert syncing.enabled()
    monkeypatch.setenv("GOTG_UI_SAVES_SYNC", "0")
    assert not syncing.enabled()


def test_a_sync_whose_client_is_missing_is_done_and_stops_quietly(monkeypatch):
    monkeypatch.setattr(syncing, "gotg_bin", lambda: "/nonexistent/gotg")
    monkeypatch.setattr(syncing.shutil, "which", lambda _name: None)
    sync = syncing.Sync()
    assert sync.done()
    sync.stop()


def test_a_handoff_lets_a_running_sync_finish_before_stopping_it(monkeypatch, tmp_path):
    script = tmp_path / "gotg"
    script.write_text("#!/bin/sh\nsleep 0.3\n")
    script.chmod(0o755)
    monkeypatch.setattr(syncing, "gotg_bin", lambda: str(script))
    monkeypatch.setattr(syncing.shutil, "which", lambda _name: None)
    sync = syncing.Sync()
    assert not sync.done()
    sync.stop(grace=5.0)
    assert sync.done() and sync.process.returncode == 0, "it finished; it was not killed"
    sync = syncing.Sync()
    sync.stop()
    sync.process.wait(timeout=5)
    assert sync.process.returncode != 0, "a quit does not wait"


def test_a_grace_that_runs_out_stops_the_whole_process_group(monkeypatch, tmp_path):
    marker = tmp_path / "child-alive"
    script = tmp_path / "gotg"
    script.write_text(f"#!/bin/sh\n(sleep 30; touch {marker}) &\nwait\n")
    script.chmod(0o755)
    monkeypatch.setattr(syncing, "gotg_bin", lambda: str(script))
    monkeypatch.setattr(syncing.shutil, "which", lambda _name: None)
    sync = syncing.Sync()
    sync.stop(grace=0.2)
    sync.process.wait(timeout=5)
    assert sync.process.returncode != 0
    assert not marker.exists()


def test_stopping_a_finished_sync_is_a_no_op(monkeypatch, tmp_path):
    script = tmp_path / "gotg"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setattr(syncing, "gotg_bin", lambda: str(script))
    monkeypatch.setattr(syncing.shutil, "which", lambda _name: None)
    sync = syncing.Sync()
    sync.process.wait(timeout=5)
    assert sync.done()
    sync.stop(grace=1.0)
    sync.stop()
    assert sync.process.returncode == 0
