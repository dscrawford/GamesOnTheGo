"""Catalog persistence."""

from gotg_importer.manifest import Entry, load, save


def entry(path="/Games/n64/usa.zelda.z64", **kw):
    defaults = dict(platform="n64", path=path, type="file", size_bytes=1, sha256="deadbeef", title="Zelda")
    defaults.update(kw)
    return Entry(**defaults)


def test_roundtrip(tmp_path):
    path = tmp_path / ".gotg" / "manifest.json"
    save(path, {"/Games/n64/usa.zelda.z64": entry()})

    loaded = load(path)

    assert loaded["/Games/n64/usa.zelda.z64"].title == "Zelda"


def test_save_merges_rather_than_replacing(tmp_path):
    path = tmp_path / "manifest.json"
    save(path, {"/Games/n64/a.z64": entry(path="/Games/n64/a.z64")})

    entries = load(path)
    entries["/Games/n64/b.z64"] = entry(path="/Games/n64/b.z64")
    save(path, entries)

    assert set(load(path)) == {"/Games/n64/a.z64", "/Games/n64/b.z64"}


def test_reimport_updates_in_place(tmp_path):
    path = tmp_path / "manifest.json"
    save(path, {"/Games/n64/a.z64": entry(path="/Games/n64/a.z64", size_bytes=1)})

    entries = load(path)
    entries["/Games/n64/a.z64"] = entry(path="/Games/n64/a.z64", size_bytes=999)
    save(path, entries)

    loaded = load(path)
    assert len(loaded) == 1
    assert loaded["/Games/n64/a.z64"].size_bytes == 999


def test_unchanged_manifest_is_not_rewritten(tmp_path):
    # The CronJob runs every five minutes; an idle run must not touch the volume.
    path = tmp_path / "manifest.json"
    entries = {"/Games/n64/a.z64": entry(path="/Games/n64/a.z64")}

    assert save(path, entries) is True
    inode = path.stat().st_ino
    assert save(path, entries) is False
    assert path.stat().st_ino == inode


def test_missing_manifest_reads_as_empty(tmp_path):
    assert load(tmp_path / "nope.json") == {}


def test_corrupt_manifest_does_not_crash_the_run(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{ this is not json")
    assert load(path) == {}


def test_unreadable_rows_are_dropped_not_fatal(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"version": 1, "games": [{"nope": true}, '
        '{"platform": "n64", "path": "/Games/n64/a.z64", "type": "file", '
        '"size_bytes": 1, "sha256": null, "title": "A"}]}'
    )
    assert list(load(path)) == ["/Games/n64/a.z64"]


def test_game_id_strips_the_extension():
    assert entry().game_id == "usa.zelda"
    assert entry(path="/Games/wiiu/usa.zelda", type="dir").game_id == "usa.zelda"
