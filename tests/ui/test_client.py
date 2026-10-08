"""Property under test: one way of asking the client, one way of failing --
the answer's text, or None, and never an exception."""

from __future__ import annotations

import subprocess

from gotg_ui import client


def test_it_runs_the_client_with_the_argv_and_returns_stdout(fake_client):
    fake_client.answer("complete", "installed", stdout="snes/a\nn64/b\n")
    assert client.ask(["complete", "installed"], timeout=5) == "snes/a\nn64/b\n"
    assert fake_client.calls == [["complete", "installed"]]


def test_the_longest_matching_row_answers(fake_client):
    fake_client.answer("complete", stdout="short")
    fake_client.answer("complete", "ready", stdout="ready")
    assert client.ask(["complete", "ready", "n64/x"], timeout=5) == "ready"
    assert client.ask(["complete", "other"], timeout=5) == "short"


def test_a_client_with_nothing_to_say_answers_the_empty_string_not_none(fake_client):
    # An older client answers an unknown subcommand with exit 0 and no output;
    # that is an answer ("nothing"), told apart from a failure by callers.
    assert client.ask(["complete", "future"], timeout=5) == ""


def test_a_failing_client_is_none(fake_client):
    fake_client.answer("complete", stdout="partial", code=1, stderr="boom")
    assert client.ask(["complete", "x"], timeout=5) is None


def test_a_missing_client_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv("GOTG_BIN", str(tmp_path / "nope"))
    assert client.ask(["complete", "x"], timeout=5) is None


def test_a_hanging_client_is_none(monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(client.subprocess, "run", hang)
    assert client.ask(["complete", "x"], timeout=1) is None


def test_the_timeout_is_the_callers(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(client.subprocess, "run", run)
    client.ask(["complete", "x"], timeout=7)
    assert seen["timeout"] == 7


def test_undecodable_output_is_read_not_a_crash(tmp_path, monkeypatch):
    script = tmp_path / "gotg"
    script.write_text("#!/bin/sh\nprintf 'ok \\377\\n'\n")
    script.chmod(0o755)
    monkeypatch.setenv("GOTG_BIN", str(script))
    assert client.ask(["complete", "x"], timeout=5).startswith("ok ")
