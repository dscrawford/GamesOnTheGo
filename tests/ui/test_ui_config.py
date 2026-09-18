"""`config/`, as one dictionary.

The utility itself: where it looks, what a directory becomes, and what happens
when a file is missing or broken. What the sections *mean* is tested beside the
code that reads them -- test_schemes.py for controllers, test_icons.py for the
icon rules.
"""

import pathlib

import pytest

from gotg_ui import config

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean():
    config.forget()
    yield
    config.forget()


def test_a_file_becomes_a_key_named_after_it(tmp_path):
    (tmp_path / "theme.yaml").write_text("colours:\n  text: [1, 2, 3]\n")
    assert config.read(tmp_path) == {"theme": {"colours": {"text": [1, 2, 3]}}}


def test_a_directory_becomes_a_key_holding_its_files(tmp_path):
    (tmp_path / "controllers").mkdir()
    (tmp_path / "controllers" / "snes.yaml").write_text("layout: snes\n")
    (tmp_path / "controllers" / "n64.yaml").write_text("layout: n64\n")
    read = config.read(tmp_path)
    assert read == {"controllers": {"snes": {"layout": "snes"}, "n64": {"layout": "n64"}}}


def test_a_broken_file_costs_its_own_section_and_nothing_else(tmp_path):
    # A picker that refused to start over a stray tab in a colour table would
    # be a worse failure than the one it is reporting.
    (tmp_path / "broken.yaml").write_text("colours: [unclosed\n")
    (tmp_path / "theme.yaml").write_text("window: [1280, 800]\n")
    read = config.read(tmp_path)
    assert "broken" not in read
    assert read["theme"] == {"window": [1280, 800]}


def test_a_directory_that_is_not_there_is_empty_rather_than_an_error(tmp_path):
    assert config.read(tmp_path / "nothing") == {}


def test_a_dotted_path_reaches_in(monkeypatch):
    monkeypatch.setenv("GOTG_CONFIG", str(REPO / "config"))
    config.forget()
    assert config.get("theme.grid.columns") == 5
    assert config.get("controllers.gamecube.layout") == "gamecube"


def test_a_missing_key_is_the_default(monkeypatch):
    monkeypatch.setenv("GOTG_CONFIG", str(REPO / "config"))
    config.forget()
    assert config.get("theme.nothing.here", "fallback") == "fallback"
    assert config.get("no_such_section") is None
    # Walking *through* a value rather than a mapping is a miss, not a crash.
    assert config.get("theme.grid.columns.deeper", "fallback") == "fallback"


def test_a_colour_comes_back_as_a_tuple(monkeypatch):
    # YAML gives a list. Mixing the two makes equality in a test depend on
    # which half wrote the value.
    monkeypatch.setenv("GOTG_CONFIG", str(REPO / "config"))
    config.forget()
    assert config.colour("theme.colours.text", (0, 0, 0)) == (232, 232, 236)


def test_a_colour_that_is_not_one_falls_back(tmp_path, monkeypatch):
    (tmp_path / "theme.yaml").write_text("colours:\n  text: purple\n  panel: [1, 2]\n")
    monkeypatch.setenv("GOTG_CONFIG", str(tmp_path))
    config.forget()
    assert config.colour("theme.colours.text", (9, 9, 9)) == (9, 9, 9)
    assert config.colour("theme.colours.panel", (9, 9, 9)) == (9, 9, 9)


def test_the_repository_config_has_the_sections_the_picker_reads():
    monkeypatch_free = config.read(REPO / "config")
    assert set(monkeypatch_free) >= {"theme", "icons", "controllers"}


# --- fullscreen, asked for by whoever launched it -----------------------------


def test_the_picker_fills_the_screen_when_it_is_asked_to(monkeypatch):
    # Steam's entry asks: from Game Mode the picker is the whole screen, not
    # a window in a corner of it.
    monkeypatch.setenv("GOTG_UI_FULLSCREEN", "1")
    assert config.fullscreen() is True


def test_at_a_desk_it_is_a_window(monkeypatch):
    monkeypatch.delenv("GOTG_UI_FULLSCREEN", raising=False)
    assert config.fullscreen() is False
    monkeypatch.setenv("GOTG_UI_FULLSCREEN", "0")
    assert config.fullscreen() is False
