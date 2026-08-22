"""The catalog the grid draws from, and the paging over it.

What matters most: the UI reads the same cache `gotg list` does and never the
network, a library of thousands pages without the caller doing arithmetic, and
the one error worth rendering — no catalog here yet — says what to run.
"""

from __future__ import annotations

import json

import pytest

from gotg_ui.catalog import CatalogError, Game, Library, load

PER_PAGE = 10


def write_cache(path, games, *, version=2):
    payload = {
        "version": version,
        "games": [
            {
                "id": g[0],
                "platform": g[1],
                "title": g[2],
                "handler": "single_file",
                "files": [{"name": f"{g[0]}.rom", "size_bytes": 4, "sha256": None}],
            }
            for g in games
        ],
    }
    path.write_text(json.dumps(payload))
    return path


def some(count, platform="snes"):
    return [(f"usa.game_{i:03}", platform, f"Game {i}") for i in range(count)]


# --- reading the cache --------------------------------------------------------


def test_a_catalog_reads_into_games(tmp_path):
    cache = write_cache(tmp_path / "manifest.json", [("usa.zelda", "n64", "Ocarina of Time")])
    games = load(cache)
    assert games == [Game(id="usa.zelda", platform="n64", title="Ocarina of Time", handler="single_file")]


def test_no_catalog_says_what_to_run(tmp_path):
    # The one error the grid has to render, so it has to name the fix.
    with pytest.raises(CatalogError, match="gotg refresh"):
        load(tmp_path / "nothing.json")


@pytest.mark.parametrize(
    "body",
    ['{"version": 1, "games": []}', "not json at all", "[]", '{"games": []}', '{"version": 2}'],
    ids=["old-version", "not-json", "not-an-object", "no-version", "no-games"],
)
def test_a_cache_this_cannot_read_is_an_error_not_an_empty_grid(tmp_path, body):
    # An empty grid and an unreadable catalog look identical on screen, and
    # only one of them is worth telling somebody about.
    cache = tmp_path / "manifest.json"
    cache.write_text(body)
    with pytest.raises(CatalogError):
        load(cache)


def test_a_row_that_makes_no_sense_is_dropped_not_fatal(tmp_path):
    # One bad row in five thousand must not cost the whole grid; the client's
    # own manifest reader drops unreadable rows the same way.
    cache = tmp_path / "manifest.json"
    cache.write_text(
        json.dumps(
            {
                "version": 2,
                "games": [
                    {"id": "usa.good", "platform": "n64", "title": "Good", "handler": "single_file"},
                    {"id": "usa.nameless", "platform": "n64", "handler": "single_file"},
                    "not even an object",
                    {"platform": "n64", "title": "No id", "handler": "single_file"},
                ],
            }
        )
    )
    assert [g.id for g in load(cache)] == ["usa.good"]


def test_the_catalogs_own_order_is_kept(tmp_path):
    # platform then id, the order the service returns and the order `gotg list`
    # prints. Two views of one library that disagree about sequence is a way to
    # lose a game you just saw.
    cache = write_cache(
        tmp_path / "manifest.json",
        [("usa.b", "gb", "B"), ("usa.a", "n64", "A"), ("usa.c", "n64", "C")],
    )
    assert [g.id for g in load(cache)] == ["usa.b", "usa.a", "usa.c"]


# --- paging -------------------------------------------------------------------


def test_ten_to_a_page(tmp_path):
    library = Library(load(write_cache(tmp_path / "m.json", some(25))))
    assert library.pages == 3
    assert [g.title for g in library.page(0)] == [f"Game {i}" for i in range(10)]
    assert len(library.page(1)) == 10
    assert len(library.page(2)) == 5, "the last page is short, not padded"


@pytest.mark.parametrize(
    ("count", "pages"),
    [(0, 0), (1, 1), (9, 1), (10, 1), (11, 2), (5674, 568)],
    ids=["empty", "one", "nine", "exactly-ten", "eleven", "the-real-library"],
)
def test_page_count_at_the_boundaries(tmp_path, count, pages):
    library = Library(load(write_cache(tmp_path / f"m{count}.json", some(count))))
    assert library.pages == pages


def test_an_empty_library_has_no_pages_and_no_rows(tmp_path):
    library = Library(load(write_cache(tmp_path / "m.json", [])))
    assert library.pages == 0
    assert library.page(0) == []


@pytest.mark.parametrize("asked", [-5, -1, 3, 900])
def test_a_page_off_either_end_clamps(tmp_path, asked):
    # The stick is held down and the page index runs off the end; clamping is
    # what stops that being an empty screen with no way back.
    library = Library(load(write_cache(tmp_path / "m.json", some(25))))
    page = library.page(asked)
    assert page, "clamped rather than empty"
    assert page in (library.page(0), library.page(library.pages - 1))


# --- filtering, which the pager is built on from the start --------------------


def test_filtering_by_platform_repages(tmp_path):
    games = some(12, "gb") + some(3, "n64")
    library = Library(load(write_cache(tmp_path / "m.json", games)))
    assert library.pages == 2

    only_n64 = library.filter(platform="n64")
    assert only_n64.pages == 1
    assert {g.platform for g in only_n64.page(0)} == {"n64"}
    assert library.pages == 2, "filtering returns a new view and leaves this one alone"


def test_searching_matches_id_and_title_without_case(tmp_path):
    cache = write_cache(
        tmp_path / "m.json",
        [
            ("usa.legend_of_zelda", "n64", "Ocarina of Time"),
            ("usa.metroid", "nes", "Metroid"),
            ("usa.zelda_ii", "nes", "Adventure of Link"),
        ],
    )
    library = Library(load(cache))
    assert {g.id for g in library.filter(search="ZELDA").page(0)} == {"usa.legend_of_zelda", "usa.zelda_ii"}
    assert {g.id for g in library.filter(search="ocarina").page(0)} == {"usa.legend_of_zelda"}


def test_platform_and_search_together_are_an_and(tmp_path):
    cache = write_cache(
        tmp_path / "m.json",
        [("usa.zelda", "n64", "Zelda"), ("usa.zelda", "snes", "Zelda"), ("usa.mario", "n64", "Mario")],
    )
    found = Library(load(cache)).filter(platform="n64", search="zelda").page(0)
    assert [(g.platform, g.id) for g in found] == [("n64", "usa.zelda")]


def test_a_filter_matching_nothing_is_an_empty_library_not_an_error(tmp_path):
    library = Library(load(write_cache(tmp_path / "m.json", some(12)))).filter(search="no such game")
    assert library.pages == 0
    assert library.page(0) == []


def test_the_platforms_on_offer_come_from_the_catalog(tmp_path):
    games = some(2, "snes") + some(2, "gb") + some(1, "n64")
    library = Library(load(write_cache(tmp_path / "m.json", games)))
    assert library.platforms == ["gb", "n64", "snes"], "sorted, so the filter row is stable between launches"
