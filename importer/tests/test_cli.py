"""CLI argument handling and the §10 exit-code contract."""

import json

from gotg_importer.cli import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, _bootstrap_paths, main

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


def test_unreachable_qbittorrent_is_a_hard_failure(monkeypatch, tmp_path):
    # §10: exit non-zero only on hard failure, and an unreachable queue is one.
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(tmp_path / "Torrents"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QBIT_URL", "http://127.0.0.1:1/")
    monkeypatch.setenv("QBIT_USER", "u")
    monkeypatch.setenv("QBIT_PASS", "p")
    assert main(["--once"]) == EXIT_CONFIG


def test_bootstrap_imports_for_real_and_writes_a_manifest(monkeypatch, tmp_path):
    src = tmp_path / "Torrents"
    src.mkdir()
    rom = src / "Legend of Zelda, The - Majora's Mask (USA).z64"
    rom.write_bytes(b"rom")
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(src))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

    assert main(["--bootstrap", str(rom)]) == EXIT_OK

    imported = tmp_path / "Games" / "n64" / "usa.legend_of_zelda_majoras_mask.z64"
    assert imported.stat().st_ino == rom.stat().st_ino
    manifest = json.loads((tmp_path / "Games" / ".gotg" / "manifest.json").read_text())
    assert manifest["games"][0]["path"] == "/Games/n64/usa.legend_of_zelda_majoras_mask.z64"


def test_a_payload_needing_review_still_exits_zero(monkeypatch, tmp_path):
    # A run that flagged something is not a failed run; the CronJob must not retry.
    src = tmp_path / "Torrents"
    (src / "mystery").mkdir(parents=True)
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(src))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

    assert main(["--bootstrap", str(src / "mystery")]) == EXIT_OK


def _publish_env(monkeypatch, tmp_path):
    src = tmp_path / "Torrents"
    src.mkdir(exist_ok=True)
    monkeypatch.setenv("GAMES_ROOT", str(tmp_path / "Games"))
    monkeypatch.setenv("SOURCE_ROOT", str(src))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GOTG_API_URL", "http://127.0.0.1:1")  # nothing listens here
    monkeypatch.setenv("GOTG_INDEX_TOKEN", "t")
    return src


def test_a_down_api_disables_publishing_and_the_import_still_lands(monkeypatch, tmp_path, caplog):
    src = _publish_env(monkeypatch, tmp_path)
    (tmp_path / "Games").mkdir()
    (tmp_path / "state").mkdir()
    rom = src / "Legend of Zelda, The - Majora's Mask (USA).z64"
    rom.write_bytes(b"rom")

    assert main(["--bootstrap", str(rom)]) == EXIT_OK

    assert (tmp_path / "Games" / "n64" / "usa.legend_of_zelda_majoras_mask.z64").exists()
    assert "catalog publishing disabled" in caplog.text


def test_a_dry_run_never_dials_the_api(monkeypatch, tmp_path, caplog):
    src = _publish_env(monkeypatch, tmp_path)
    (tmp_path / "Games").mkdir()
    (tmp_path / "state").mkdir()
    rom = src / "Legend of Zelda, The - Majora's Mask (USA).z64"
    rom.write_bytes(b"rom")

    assert main(["--dry-run", "--bootstrap", str(rom)]) == EXIT_OK

    assert "catalog publishing disabled" not in caplog.text, "a dry run must not construct a publisher"


def test_diff_catalog_against_a_down_api_is_a_hard_failure(monkeypatch, tmp_path):
    _publish_env(monkeypatch, tmp_path)
    (tmp_path / "Games").mkdir()
    (tmp_path / "state").mkdir()
    assert main(["--diff-catalog"]) == EXIT_FAILED
