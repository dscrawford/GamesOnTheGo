"""Which versions of a game the picker offers.

The list comes from the client, so what is tested here is the parsing and what
the menu does with it — including the case the whole feature exists for: a mod
that no installed version suits, where the client marks nothing as running.
"""

from __future__ import annotations

import pytest

from gotg_ui.catalog import Game
from gotg_ui.launch import command_for
from gotg_ui.menu import AUTOMATIC, MODS, VERSIONS, Menu
from gotg_ui.versions import forget, names, running, versions_for


@pytest.fixture(autouse=True)
def _fresh():
    """The client's answer is cached for the session, so a test that stubs a
    different client has to say so. Real code invalidates on an uninstall;
    here every test is a different machine."""
    forget()
    yield
    forget()


def game(id="world.totk", platform="switch"):
    return Game(id=id, platform=platform, title="A Game", handler="single_archive")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A stand-in `gotg complete versions`, recording how it was called."""
    log = tmp_path / "argv"
    script = tmp_path / "gotg"
    script.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >>" + str(log) + "\nprintf '*1.4.3\\n1.4.2\\n1.2.1\\n'\n")
    script.chmod(0o755)
    monkeypatch.setenv("GOTG_BIN", str(script))
    return log


def test_versions_come_back_newest_first_with_the_running_one_marked(client):
    rows = versions_for(game())
    assert names(rows) == ("1.4.3", "1.4.2", "1.2.1")
    assert running(rows) == "1.4.3"


def test_the_variant_is_passed_on_so_a_mod_s_ceiling_is_accounted_for(client):
    versions_for(game(), "60fps")
    assert client.read_text().strip().endswith("complete versions switch/world.totk 60fps")


def test_a_client_that_cannot_answer_is_a_game_with_no_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("GOTG_BIN", str(tmp_path / "not-here"))
    assert versions_for(game()) == ()


def test_nothing_marked_means_the_client_would_refuse(monkeypatch, tmp_path):
    # A mod with no version old enough: the client marks none of them, and the
    # picker must not invent one.
    script = tmp_path / "gotg"
    script.write_text("#!/bin/sh\nprintf '1.4.3\\n'\n")
    script.chmod(0o755)
    monkeypatch.setenv("GOTG_BIN", str(script))
    rows = versions_for(game())
    assert names(rows) == ("1.4.3",)
    assert running(rows) is None


# --- in the menu -------------------------------------------------------------


def with_versions(*versions, variants=()):
    return Menu(game(), 0, installed=True, variants=variants, versions=versions)


def test_one_version_is_not_a_choice_worth_a_row():
    assert all(verb != VERSIONS for _, verb in with_versions("1.4.3").actions)


def test_several_versions_get_a_row_that_names_the_current_one():
    m = with_versions("1.4.3", "1.4.2")
    label, verb = m.actions[0]
    assert verb == VERSIONS and label == f"Version: {AUTOMATIC}"


def test_the_row_opens_into_the_versions_newest_first():
    m = with_versions("1.4.3", "1.4.2", "1.2.1")
    assert m.confirm() is None
    assert [label for label, _ in m.actions] == ["Back", AUTOMATIC, "1.4.3", "1.4.2", "1.2.1"]


def test_choosing_a_version_comes_back_and_carries_into_the_verbs():
    m = with_versions("1.4.3", "1.4.2")
    m.confirm()
    m.select(3)  # Back, automatic, 1.4.3, 1.4.2
    assert m.confirm() is None
    assert m.version == "1.4.2"
    assert m.actions[0][0] == "Version: 1.4.2"

    m.select(1)  # Play Game, under the version row
    assert m.confirm() == "play"
    assert command_for(m.game, "play", m.variant, m.version)[-2:] == ["--version", "1.4.2"]


def test_automatic_leaves_the_choice_to_the_client():
    m = with_versions("1.4.3", "1.4.2")
    m.confirm()
    m.select(2)
    m.confirm()
    assert m.version == "1.4.3"
    m.confirm()  # open it again
    m.select(1)  # automatic
    m.confirm()
    assert m.version is None
    assert "--version" not in command_for(m.game, "play", None, None)


def test_mods_and_versions_are_separate_rows_and_separate_lists():
    m = with_versions("1.4.3", "1.4.2", variants=("60fps",))
    assert [verb for _, verb in m.actions[:2]] == [MODS, VERSIONS]

    m.confirm()  # the mods row
    m.select(2)  # 60fps
    m.confirm()
    assert m.variant == "60fps" and m.version is None

    m.select(1)  # the version row, now second
    m.confirm()
    m.select(2)  # 1.4.3
    m.confirm()
    assert (m.variant, m.version) == ("60fps", "1.4.3")
    assert command_for(m.game, "play", m.variant, m.version)[2:] == [
        "switch/world.totk",
        "60fps",
        "--version",
        "1.4.3",
    ]


def test_backing_out_of_the_version_list_keeps_what_was_chosen():
    m = with_versions("1.4.3", "1.4.2")
    m.confirm()
    m.select(3)
    m.confirm()
    assert m.version == "1.4.2"
    m.confirm()
    m.select(0)  # Back
    assert m.confirm() is None
    assert m.version == "1.4.2" and m.expanded is None


def test_the_client_is_asked_once_per_game(monkeypatch):
    """Opening a menu spawned `gotg complete versions` every time: 31 ms on a
    Steam Deck, on the press that opens the menu, which is where a stall shows.
    The answer only changes when something is installed or removed."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Done", (), {"returncode": 0, "stdout": "*1.4.3\n1.2.1\n"})()

    monkeypatch.setattr("gotg_ui.versions.subprocess.run", fake_run)
    assert versions_for(game()) == (("1.4.3", True), ("1.2.1", False))
    assert versions_for(game()) == (("1.4.3", True), ("1.2.1", False))
    assert len(calls) == 1


def test_a_variant_is_its_own_question(monkeypatch):
    # A mod can take a different version from the plain game, so the two must
    # not share an answer.
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Done", (), {"returncode": 0, "stdout": "*1.4.3\n"})()

    monkeypatch.setattr("gotg_ui.versions.subprocess.run", fake_run)
    versions_for(game())
    versions_for(game(), "bse")
    assert len(calls) == 2


def test_a_client_that_could_not_answer_is_asked_again(monkeypatch):
    # Caching the silence would make one busy moment permanent.
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        raise OSError("busy")

    monkeypatch.setattr("gotg_ui.versions.subprocess.run", fake_run)
    assert versions_for(game()) == ()
    assert versions_for(game()) == ()
    assert len(calls) == 2
