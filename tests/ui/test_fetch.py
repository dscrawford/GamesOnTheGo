"""The loader's fence behaviour: a name the cache refuses must cost one tile
its picture, never the grid its life.

Only the non-network half is tested here; the sources themselves are the
client's, covered from its side.
"""

from __future__ import annotations

import pytest

from gotg_ui.art import ArtStore
from gotg_ui.catalog import Game
from gotg_ui.fetch import Loader


@pytest.fixture
def loader(tmp_path, monkeypatch):
    # No api.json anywhere: url/token come up empty, which is the machine
    # that never logged in — sources still construct, nothing is fetched here.
    monkeypatch.setenv("GOTG_API_FILE", str(tmp_path / "absent.json"))
    return Loader(ArtStore(tmp_path / "art"), workers=0)


def test_a_hostile_name_is_no_art_not_a_crash(loader):
    # This exact shape took the whole grid down from the frame loop once: the
    # cache's ValueError fence rode up through want() on a catalog row the
    # fence disliked. The fence must hold — no path built — quietly.
    bad = Game(id="../escape", platform="gba", title="t", handler="single_file")
    assert loader.want(bad) is None
    assert loader.want(bad) is None  # and again every frame, still quietly


def test_a_real_long_id_is_wanted_normally(loader):
    g = Game(id="jpn." + "x" * 100, platform="gba", title="t", handler="single_file")
    assert loader.want(g) is None  # queued for fetch, no picture yet
    assert g.key in loader._seen, "requested, not rejected"
