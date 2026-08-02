"""Run-level behaviour: durability and cleanup when a run does not finish.

These cover what a real bootstrap hit — an OOM kill during a multi-gigabyte
extract, after thousands of games had already imported successfully.
"""

import json

import pytest

from gotg_importer.config import load as load_config
from gotg_importer.execute import cleanup_staging
from gotg_importer.rules import defaults
from gotg_importer.run import run_paths

RULES = defaults()


@pytest.fixture
def cfg(tmp_path):
    games = tmp_path / "Games"
    source = tmp_path / "Torrents"
    games.mkdir()
    source.mkdir()
    return load_config(
        {
            "GAMES_ROOT": str(games),
            "SOURCE_ROOT": str(source),
            "STATE_DIR": str(tmp_path / "state"),
        },
        require_qbit=False,
    )


def make_set(cfg, dirname, filenames):
    d = cfg.source_root / dirname
    d.mkdir()
    for name in filenames:
        (d / name).write_bytes(b"rom")
    return d


def test_catalog_survives_a_run_that_dies_later(cfg):
    # The first source imports; the second blows up. What already succeeded must
    # still be in the catalog, or the games are on disk with nothing naming them.
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    exploding = cfg.source_root / "second"
    exploding.mkdir()

    import gotg_importer.run as runmod

    real = runmod.process_source
    calls = {"n": 0}

    def blow_up_on_second(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise MemoryError("simulated OOM")
        return real(path, *args, **kwargs)

    runmod.process_source = blow_up_on_second
    try:
        with pytest.raises(MemoryError):
            run_paths(
                [cfg.source_root / "Nintendo - Nintendo 64 (BigEndian)", exploding],
                cfg,
                RULES,
                dry_run=False,
            )
    finally:
        runmod.process_source = real

    manifest = json.loads(cfg.manifest_path.read_text())
    assert [g["path"] for g in manifest["games"]] == ["/Games/n64/usa.body_harvest.zip"]


def test_catalog_is_written_after_every_source(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    make_set(cfg, "Nintendo - Super Nintendo Entertainment System", ["Zelda (USA).zip"])

    run_paths(
        [
            cfg.source_root / "Nintendo - Nintendo 64 (BigEndian)",
            cfg.source_root / "Nintendo - Super Nintendo Entertainment System",
        ],
        cfg,
        RULES,
        dry_run=False,
    )

    manifest = json.loads(cfg.manifest_path.read_text())
    assert len(manifest["games"]) == 2


def test_staging_from_a_killed_run_is_cleaned_up(cfg):
    # SIGKILL skips Python's temp-dir cleanup, so these would strand gigabytes.
    stale = cfg.games_root / "switch" / ".gotg-extract-abc123"
    stale.mkdir(parents=True)
    (stale / "partial.nsp").write_bytes(b"x" * 1000)
    keep = cfg.games_root / "switch" / "world.real_game.nsp"
    keep.write_bytes(b"rom")

    removed = cleanup_staging(cfg.games_root)

    assert removed == 1
    assert not stale.exists()
    assert keep.exists(), "cleanup must not touch imported games"


def test_cleanup_runs_before_importing(cfg):
    stale = cfg.games_root / "n64" / ".gotg-zip-xyz"
    stale.mkdir(parents=True)
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])

    run_paths([cfg.source_root / "Nintendo - Nintendo 64 (BigEndian)"], cfg, RULES, dry_run=False)

    assert not stale.exists()


def test_cleanup_on_a_missing_games_root_is_harmless(tmp_path):
    assert cleanup_staging(tmp_path / "nope") == 0


def test_scan_finds_games_and_walks_past_everything_else(tmp_path):
    """The source tree is shared with film and television: a TV episode is not a
    low-confidence game, it is not a game, and must not become a manual item."""
    from gotg_importer.rules import load as load_rules
    from gotg_importer.run import discover

    root = tmp_path / "Torrents"
    root.mkdir()
    (root / "Some.Show.S01E01.1080p.WEB-DL.x264-GROUP.mkv").write_bytes(b"x")
    (root / "Some.Film.2024.2160p.BluRay.REMUX.HEVC.mkv").write_bytes(b"x")
    (root / "usa.zelda.z64").write_bytes(b"rom")
    dat = root / "Nintendo - Nintendo 64 (BigEndian)"
    dat.mkdir()
    for n in range(6):
        (dat / f"Game {n} (USA).z64").write_bytes(b"rom")

    found, ignored = discover(root, load_rules())

    names = {p.name for p in found}
    assert "usa.zelda.z64" in names
    assert "Nintendo - Nintendo 64 (BigEndian)" in names
    assert ignored == 2
    assert not any(n.endswith(".mkv") for n in names)
