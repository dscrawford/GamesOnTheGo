"""Narrowing the library from inside the grid.

568 pages is what makes this the difference between a picker and a list you
scroll past. The trap it has to avoid is stale state: a filter applied while
the cursor is on page 300 must not leave it there.
"""

from __future__ import annotations

import pytest

from gotg_ui.browser import ALL, Browser
from gotg_ui.catalog import Game, Library


def library(spec):
    games = []
    for platform, count in spec.items():
        games += [
            Game(id=f"usa.{platform}_{i:03}", platform=platform, title=f"{platform.upper()} Game {i}", handler="x")
            for i in range(count)
        ]
    return Library(games)


@pytest.fixture
def browser():
    return Browser(library({"snes": 25, "n64": 12, "gb": 3}))


# --- the platform filter ------------------------------------------------------


def test_it_starts_showing_everything():
    b = Browser(library({"snes": 25, "n64": 12, "gb": 3}))
    assert b.platform == ALL
    assert len(b.visible) == 40


def test_all_comes_first_and_the_rest_are_sorted(browser):
    # Sorted so the order does not shuffle between launches, and "all" pinned
    # to the front so one press from the start widens rather than narrows.
    assert browser.platforms == [ALL, "gb", "n64", "snes"]


def test_cycling_walks_the_platforms_and_wraps(browser):
    seen = []
    for _ in range(5):
        browser.cycle_platform(1)
        seen.append(browser.platform)
    assert seen == ["gb", "n64", "snes", ALL, "gb"]


def test_cycling_backwards_wraps_the_other_way(browser):
    browser.cycle_platform(-1)
    assert browser.platform == "snes"


def test_filtering_narrows_what_is_visible(browser):
    browser.cycle_platform(1)
    assert browser.platform == "gb"
    assert len(browser.visible) == 3
    assert {g.platform for g in browser.grid.page} == {"gb"}


def test_a_platform_with_one_page_has_one_page(browser):
    browser.set_platform("gb")
    assert browser.grid.library.pages == 1


# --- the stale-state trap -----------------------------------------------------


def test_filtering_from_a_deep_page_lands_somewhere_real(browser):
    # The whole point. Page 3 of 4 across everything, then a filter with one
    # page — leaving page_index at 3 would be a blank screen and no way back.
    browser.grid.turn(1)
    browser.grid.turn(1)
    assert browser.grid.page_index == 2

    browser.set_platform("gb")
    assert browser.grid.page_index == 0
    assert browser.grid.page, "a real page, not an empty one"
    assert browser.grid.game is not None


def test_the_cursor_never_survives_onto_a_shorter_page(browser):
    browser.grid.selected = 9
    browser.set_platform("gb")  # three games
    assert browser.grid.selected < len(browser.grid.page)
    assert browser.grid.game is not None


def test_searching_also_resets_the_page(browser):
    browser.grid.turn(1)
    browser.set_search("snes game 1")
    assert browser.grid.page_index == 0
    assert browser.grid.game is not None


# --- search -------------------------------------------------------------------


def test_search_matches_id_and_title_without_case(browser):
    browser.set_search("N64")
    assert len(browser.visible) == 12
    assert {g.platform for g in browser.grid.page} == {"n64"}


def test_search_and_platform_compose(browser):
    browser.set_platform("snes")
    browser.set_search("game 1")
    ids = {g.id for g in browser.visible.games}
    assert all(i.startswith("usa.snes_") for i in ids)
    assert len(ids) == 11, "1 and 10-19"


def test_clearing_the_search_restores_the_rest(browser):
    browser.set_search("gb")
    assert len(browser.visible) == 3
    browser.set_search("")
    assert len(browser.visible) == 40


def test_a_search_matching_nothing_is_an_empty_grid_not_a_crash(browser):
    browser.set_search("no such game anywhere")
    assert len(browser.visible) == 0
    assert browser.status, "there is still a line to draw under an empty grid"
    assert browser.grid.page == []
    assert browser.grid.game is None
    browser.grid.move(1, 0)
    assert browser.grid.turn(1) is False


def test_clearing_a_filter_that_emptied_the_grid_brings_it_back(browser):
    browser.set_search("nothing matches this")
    assert browser.grid.game is None
    browser.set_search("")
    assert browser.grid.game is not None


# --- what the status line says ------------------------------------------------


def test_the_status_says_what_is_being_looked_at(browser):
    assert "40 games" in browser.status
    browser.set_platform("gb")
    assert "gb" in browser.status
    browser.set_search("game 1")
    assert "game 1" in browser.status


def test_the_status_of_an_empty_result_says_so(browser):
    browser.set_search("nothing at all")
    assert "no games" in browser.status.lower()


# --- the region switch ---------------------------------------------------------


def library_with_regions():
    games = [
        Game(id="usa.zelda", platform="n64", title="Zelda", handler="x"),
        Game(id="eur.asterix", platform="gb", title="Asterix", handler="x"),
        Game(id="world.tetris", platform="gb", title="Tetris", handler="x"),
    ]
    return Library(games)


def test_region_starts_wide_and_cycles_with_wrap():
    b = Browser(library_with_regions())
    assert b.region == ALL
    seen = [b.cycle_region(1) or b.region for _ in range(4)]
    assert seen == ["eur", "usa", "world", ALL]


def test_region_narrows_and_world_rides_along():
    b = Browser(library_with_regions())
    b.set_region("usa")
    assert {g.id for g in b.visible.games} == {"usa.zelda", "world.tetris"}


def test_region_resets_the_page_like_every_other_filter():
    games = [Game(id=f"usa.g{i:03}", platform="gb", title=f"G{i}", handler="x") for i in range(25)]
    b = Browser(Library(games))
    b.grid.turn(1)
    b.set_region("usa")
    assert b.grid.page_index == 0
    assert b.grid.game is not None


def test_the_status_names_the_region_when_one_is_set():
    b = Browser(library_with_regions())
    assert "region" not in b.status
    b.set_region("eur")
    assert "region eur+world" in b.status
    b.set_region("world")
    assert "region world" in b.status


def test_a_region_the_catalog_does_not_hold_is_refused():
    # set_region on "jpn" when nothing is jpn: a no-op, like set_platform —
    # the switch only offers what exists, so a stale saved value cannot land
    # the grid on a filter with no off ramp.
    b = Browser(library_with_regions())
    b.set_region("jpn")
    assert b.region == ALL
