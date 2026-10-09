"""Property under test: the picker opens on the filters it was closed with,
and a file that is missing or wrong leaves it on the whole library."""

from __future__ import annotations

import json

from gotg_ui import remembered
from gotg_ui.browser import ALL, INSTALLED, SHELF, Browser
from gotg_ui.catalog import Game, Library


def browser():
    return Browser(
        Library([
            Game(id="usa.zelda", platform="n64", title="Zelda", handler="rom"),
            Game(id="jpn.mother", platform="snes", title="Mother 2", handler="rom"),
        ]),
        per_page=10,
    )


def test_what_was_set_comes_back(tmp_path):
    ours = browser()
    ours.set_platform("snes")
    ours.set_region("jpn")
    ours.set_presence(INSTALLED)
    ours.set_search("moth")
    ours.set_view(SHELF)
    kept = remembered.Remembered.open(tmp_path / "picker.json").keep(remembered.snapshot(ours))

    fresh = browser()
    remembered.apply(fresh, remembered.Remembered.open(kept.where).last)
    assert remembered.snapshot(fresh) == remembered.snapshot(ours)


def test_no_file_is_the_whole_library(tmp_path):
    ours = browser()
    remembered.apply(ours, remembered.Remembered.open(tmp_path / "none.json").last)
    assert (ours.platform, ours.region, ours.presence, ours.search) == (ALL, ALL, ALL, "")


def test_a_bad_file_or_a_platform_this_library_lacks_is_ignored(tmp_path):
    where = tmp_path / "picker.json"
    where.write_text("{not json")
    assert remembered.Remembered.open(where).last == {}
    where.write_text(json.dumps({"platform": "ps2", "presence": 3, "view": "cube", "search": "x", "extra": 1}))
    ours = browser()
    remembered.apply(ours, remembered.load(where))
    assert (ours.platform, ours.presence, ours.view, ours.search) == (ALL, ALL, "grid", "x")


def test_it_writes_only_when_something_changed(tmp_path):
    where = tmp_path / "picker.json"
    ours = browser()
    kept = remembered.Remembered.open(where).keep(remembered.snapshot(ours))
    stamp = where.stat().st_mtime_ns
    where.write_text("stale")
    assert kept.keep(remembered.snapshot(ours)) is kept
    assert where.read_text() == "stale"
    ours.set_platform("n64")
    kept = kept.keep(remembered.snapshot(ours))
    assert json.loads(where.read_text())["platform"] == "n64"
    assert where.stat().st_mtime_ns >= stamp


def test_a_directory_that_cannot_be_written_is_not_an_error(tmp_path):
    where = tmp_path / "file-not-dir"
    where.write_text("")
    assert remembered.save(where / "picker.json", {"a": "b"}) is False
