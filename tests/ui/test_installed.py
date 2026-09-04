"""What is downloaded here, as the grid learns it from the client.

The client owns the definition; the property held here is the seam — the
argv, the line shape, and that every way of asking failing lands as "no
badges" rather than a crash in the frame loop.
"""

from __future__ import annotations

import json
import stat

import pytest

from gotg_ui import __main__ as cli
from gotg_ui.installed import installed_games


def stub(tmp_path, body: str, monkeypatch) -> str:
    path = tmp_path / "gotg"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GOTG_BIN", str(path))
    return str(path)


def test_it_asks_complete_installed_and_reads_platform_id_lines(tmp_path, monkeypatch):
    stub(tmp_path, f'echo "$@" > {tmp_path}/argv; printf "snes/world.super_metroid\\nn64/usa.zelda\\n"', monkeypatch)
    assert installed_games() == {("snes", "world.super_metroid"), ("n64", "usa.zelda")}
    assert (tmp_path / "argv").read_text().split() == ["complete", "installed"]


def test_a_line_without_a_platform_is_not_a_key(tmp_path, monkeypatch):
    stub(tmp_path, 'printf "usa.zelda\\n/\\nsnes/\\n\\nn64/usa.ok\\n"', monkeypatch)
    assert installed_games() == {("n64", "usa.ok")}


def test_a_failing_client_means_no_badges_not_a_crash(tmp_path, monkeypatch):
    stub(tmp_path, "echo boom >&2; exit 1", monkeypatch)
    assert installed_games() == set()


def test_a_missing_client_means_no_badges(monkeypatch, tmp_path):
    monkeypatch.setenv("GOTG_BIN", str(tmp_path / "nope"))
    assert installed_games() == set()


# --- --installed on the command line ------------------------------------------


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "games": [
                    {"id": "usa.zelda", "platform": "n64", "title": "Zelda", "handler": "single_file"},
                    {"id": "usa.mario", "platform": "n64", "title": "Mario", "handler": "single_file"},
                    {"id": "world.metroid", "platform": "snes", "title": "Metroid", "handler": "single_file"},
                ],
            }
        )
    )
    return path


def test_list_installed_shows_only_what_the_client_says_is_here(tmp_path, monkeypatch, catalog, capsys):
    stub(tmp_path, 'printf "n64/usa.mario\\n"', monkeypatch)
    assert cli.main(["--catalog", str(catalog), "--installed", "--list"]) == 0
    out = capsys.readouterr()
    assert "usa.mario" in out.out
    assert "usa.zelda" not in out.out
    assert "world.metroid" not in out.out
    assert "1 games" in out.err


def test_installed_composes_with_the_other_filters(tmp_path, monkeypatch, catalog, capsys):
    stub(tmp_path, 'printf "n64/usa.mario\\nsnes/world.metroid\\n"', monkeypatch)
    assert cli.main(["--catalog", str(catalog), "--installed", "--platform", "snes", "--list"]) == 0
    out = capsys.readouterr()
    assert "world.metroid" in out.out
    assert "usa.mario" not in out.out
