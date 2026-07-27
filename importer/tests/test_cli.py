"""CLI argument handling and the §10 exit-code contract."""

import pytest

from gotg_importer.cli import EXIT_CONFIG, EXIT_OK, _bootstrap_paths, main

ENV = {
    "GAMES_ROOT": "/data/Games",
    "SOURCE_ROOT": "/data/Torrents",
    "STATE_DIR": "/state",
}


def test_comma_list_is_split(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    paths = _bootstrap_paths([f"{tmp_path}/a,{tmp_path}/b"])
    assert [p.name for p in paths] == ["a", "b"]


def test_existing_path_with_commas_is_not_split(tmp_path):
    # No-Intro names are full of commas; an existing path always wins.
    name = "Legend of Zelda, The - Twilight Princess HD (USA) (En,Fr,Es)"
    (tmp_path / name).mkdir()
    paths = _bootstrap_paths([str(tmp_path / name)])
    assert [p.name for p in paths] == [name]


def test_multiple_arguments_are_kept_separate(tmp_path):
    names = ["Zelda, The (USA)", "Metroid (USA)"]
    for n in names:
        (tmp_path / n).mkdir()
    paths = _bootstrap_paths([str(tmp_path / n) for n in names])
    assert [p.name for p in paths] == names


def test_no_mode_selected_is_a_config_error(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    assert main([]) == EXIT_CONFIG


def test_missing_source_is_a_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(tmp_path / "Torrents"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    assert main(["--dry-run", "--bootstrap", str(tmp_path / "nope")]) == EXIT_CONFIG


def test_dry_run_prints_a_plan_and_exits_zero(monkeypatch, tmp_path, capsys):
    src = tmp_path / "Torrents"
    src.mkdir()
    (src / "Legend of Zelda, The - Majora's Mask (USA).z64").write_bytes(b"x")
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(src))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

    code = main(["--dry-run", "--bootstrap", str(src / "Legend of Zelda, The - Majora's Mask (USA).z64")])

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "hardlink" in out
    assert "usa.legend_of_zelda_majoras_mask" in out
    assert not (tmp_path / "Games").exists(), "a dry run must write nothing"


@pytest.mark.parametrize("args", [["--once"], ["--bootstrap", "/tmp"]])
def test_unimplemented_paths_fail_loudly_rather_than_silently(monkeypatch, tmp_path, args):
    # Execution and queue polling arrive in phase 2; until then they must not
    # pretend to succeed.
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(tmp_path / "Torrents"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QBIT_URL", "http://qbit:8080")
    monkeypatch.setenv("QBIT_USER", "u")
    monkeypatch.setenv("QBIT_PASS", "p")
    assert main(args) == EXIT_CONFIG
