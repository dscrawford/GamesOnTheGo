"""The trace line that says how long a hold's readings went quiet.

The flash back to the red empty seat was a pause in danstick's readings longer
than the screen's patience, and how long danstick pauses depends on how many
input devices the machine has -- a pod has almost none and never paused past
25 ms. So the machine that shows the bug has to be the one that measures it.
"""

from __future__ import annotations

import json

from gotg_ui import trace


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _reading(frac: float, node: str = "/dev/input/event9") -> dict:
    return {"event": "progress", "node": node, "frac": frac}


def test_a_steady_hold_says_nothing(tmp_path, monkeypatch):
    out = tmp_path / "trace.log"
    monkeypatch.setattr(trace, "_path", str(out))
    monkeypatch.setattr(trace, "_heard", {})
    for tick in range(20):
        trace.progress_gap(_reading(0.01 * (tick + 1)), now=10.0 + tick * 0.02)
    assert _lines(out) == []


def test_a_pause_longer_than_the_screen_waits_is_one_line(tmp_path, monkeypatch):
    out = tmp_path / "trace.log"
    monkeypatch.setattr(trace, "_path", str(out))
    monkeypatch.setattr(trace, "_heard", {})
    trace.progress_gap(_reading(0.3), now=10.00)
    trace.progress_gap(_reading(0.31), now=10.02)
    trace.progress_gap(_reading(0.4), now=10.14)  # a 120 ms rescan
    said = _lines(out)
    assert [(line["kind"], line["ms"]) for line in said] == [("progress-gap", 120)]


def test_a_release_is_not_a_gap_before_the_next_press(tmp_path, monkeypatch):
    """Let go, wait, press again: the time between is nobody's pause."""
    out = tmp_path / "trace.log"
    monkeypatch.setattr(trace, "_path", str(out))
    monkeypatch.setattr(trace, "_heard", {})
    trace.progress_gap(_reading(0.5), now=10.0)
    trace.progress_gap(_reading(0.0), now=10.02)
    trace.progress_gap(_reading(0.05), now=12.0)
    assert _lines(out) == []


def test_off_means_off(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "_path", "")
    monkeypatch.setattr(trace, "_heard", {})
    trace.progress_gap(_reading(0.3), now=10.0)
    trace.progress_gap(_reading(0.4), now=11.0)
    assert trace._heard == {}
