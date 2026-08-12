"""Run-level behaviour: durability and cleanup when a run does not finish.

These cover what a real bootstrap hit — an OOM kill during a multi-gigabyte
extract, after thousands of games had already imported successfully.
"""

import json
import re

import pytest

from gotg_importer.config import load as load_config
from gotg_importer.execute import STATUS_ERROR, Result, cleanup_staging
from gotg_importer.plan import ACTION_HARDLINK, Op
from gotg_importer.publish import Collision, PublishError
from gotg_importer.rules import defaults
from gotg_importer.run import run_paths, run_scan

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


ISO_UTC = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"


class FakePublisher:
    """The two methods run.py touches, recorded, with injectable failures."""

    def __init__(self, publish_error=None, sweep_error=None):
        self.publish_error = publish_error
        self.sweep_error = sweep_error
        self.published = []
        self.swept = []

    def publish(self, result, *, checksum=True):
        self.published.append(result.op.entry_id)
        if self.publish_error:
            raise self.publish_error

    def sweep(self, since):
        self.swept.append(since)
        if self.sweep_error:
            raise self.sweep_error


def test_run_paths_keeps_importing_when_every_publish_raises(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    make_set(cfg, "Nintendo - Super Nintendo Entertainment System", ["Zelda (USA).zip"])
    publisher = FakePublisher(publish_error=PublishError("the API fell over"))

    stats = run_paths(
        [
            cfg.source_root / "Nintendo - Nintendo 64 (BigEndian)",
            cfg.source_root / "Nintendo - Super Nintendo Entertainment System",
        ],
        cfg,
        RULES,
        dry_run=False,
        publisher=publisher,
    )

    manifest = json.loads(cfg.manifest_path.read_text())
    assert len(manifest["games"]) == 2, "a publish failure must never stop the import"
    assert stats.publish_errors == 2
    assert not stats.failed
    assert "unpublished=2" in stats.summary()


def test_a_collision_rides_the_same_per_entry_path(cfg):
    # Collision subclasses PublishError; the old hardlink no-clobber error must
    # not become the one publish failure that kills a run.
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    publisher = FakePublisher(publish_error=Collision("already points at different bytes"))

    stats = run_paths(
        [cfg.source_root / "Nintendo - Nintendo 64 (BigEndian)"], cfg, RULES, dry_run=False, publisher=publisher
    )

    assert stats.publish_errors == 1
    assert json.loads(cfg.manifest_path.read_text())["games"]


def test_bootstrap_publishes_but_never_sweeps(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    publisher = FakePublisher()

    run_paths([cfg.source_root / "Nintendo - Nintendo 64 (BigEndian)"], cfg, RULES, dry_run=False, publisher=publisher)

    assert publisher.published == ["usa.body_harvest"]
    assert publisher.swept == [], "only a full enumeration may sweep; --bootstrap is not one"


def test_a_clean_scan_sweeps_once_with_a_pre_scan_timestamp(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    publisher = FakePublisher()

    stats = run_scan(cfg.source_root, cfg, RULES, dry_run=False, publisher=publisher)

    assert stats.publish_errors == 0
    assert len(publisher.swept) == 1
    assert re.fullmatch(ISO_UTC, publisher.swept[0])


def test_a_dry_run_scan_neither_publishes_nor_sweeps(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    publisher = FakePublisher()

    run_scan(cfg.source_root, cfg, RULES, dry_run=True, publisher=publisher)

    assert publisher.published == []
    assert publisher.swept == []


def test_a_scan_with_publish_errors_does_not_sweep(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    publisher = FakePublisher(publish_error=PublishError("boom"))

    stats = run_scan(cfg.source_root, cfg, RULES, dry_run=False, publisher=publisher)

    assert stats.publish_errors == 1
    assert publisher.swept == [], "an unpublished game would read as vanished"


def test_a_scan_with_a_failed_import_does_not_sweep(cfg, monkeypatch):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])

    def fail(path, *args, **kwargs):
        op = Op(ACTION_HARDLINK, "n64", str(path), "", "usa.body_harvest")
        return [Result(op, STATUS_ERROR, "disk full")]

    monkeypatch.setattr("gotg_importer.run.process_source", fail)
    publisher = FakePublisher()

    stats = run_scan(cfg.source_root, cfg, RULES, dry_run=False, publisher=publisher)

    assert stats.failed
    assert publisher.swept == []


def test_a_failing_sweep_is_counted_not_fatal(cfg):
    make_set(cfg, "Nintendo - Nintendo 64 (BigEndian)", ["Body Harvest (USA).zip"])
    publisher = FakePublisher(sweep_error=PublishError("sweep would report 900 of 1000 entries vanished"))

    stats = run_scan(cfg.source_root, cfg, RULES, dry_run=False, publisher=publisher)

    assert stats.publish_errors == 1
    assert "unpublished=1" in stats.summary()
