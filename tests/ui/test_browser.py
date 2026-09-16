"""Narrowing the library from inside the grid.

568 pages is what makes this the difference between a picker and a list you
scroll past. The trap it has to avoid is stale state: a filter applied while
the cursor is on page 300 must not leave it there.
"""

from __future__ import annotations

import pytest

from gotg_ui.browser import ALL, INSTALLED, MISSING, Browser
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


# --- the installed filter ------------------------------------------------------


def _installed_browser():
    lib = library({"snes": 25, "n64": 12})
    here = {("snes", "usa.snes_001"), ("snes", "usa.snes_002"), ("n64", "usa.n64_000")}
    return Browser(lib, installed=here), here


def test_it_starts_showing_everything_with_badges_known():
    b, here = _installed_browser()
    assert b.installed_only is False
    assert len(b.visible) == 37
    assert b.is_installed(b.visible.games[1]) is True
    assert b.is_installed(b.visible.games[5]) is False


def test_toggling_narrows_to_what_is_here_and_resets_the_cursor():
    b, here = _installed_browser()
    b.grid.turn(2)
    b.toggle_installed()
    assert b.installed_only is True
    assert {g.key for g in b.visible.games} == here
    assert b.grid.page_index == 0
    b.toggle_installed()
    assert len(b.visible) == 37


def test_it_composes_with_the_platform_filter():
    b, _ = _installed_browser()
    b.toggle_installed()
    b.set_platform("n64")
    assert [g.key for g in b.visible.games] == [("n64", "usa.n64_000")]


def test_the_status_line_says_so():
    b, _ = _installed_browser()
    assert "installed" not in b.status
    b.toggle_installed()
    assert "installed" in b.status


def test_the_status_line_names_which_way_round_it_is():
    # "installed" and "not installed" both narrow, and a line that said only
    # that something was narrowed would not say which half was on screen.
    b, _ = _installed_browser()
    b.set_presence(MISSING)
    assert "not installed" in b.status


def test_an_uninstall_updates_the_view_when_the_filter_is_on():
    b, here = _installed_browser()
    b.toggle_installed()
    assert len(b.visible) == 3
    b.set_installed(here - {("snes", "usa.snes_001")})
    assert len(b.visible) == 2
    assert b.is_installed(b.library.games[1]) is False


def test_an_uninstall_leaves_the_cursor_alone_when_the_filter_is_off():
    b, here = _installed_browser()
    b.grid.turn(2)
    b.set_installed(set())
    assert b.grid.page_index == 2
    assert b.installed == set()


# --- asking for what is *not* here -------------------------------------------


def test_not_installed_is_the_other_half():
    """The question a yes/no toggle could not ask.

    Browsing for something new is asking which games are *not* here, and the
    only answers available were "everything" and "what I already have".
    """
    b, here = _installed_browser()
    everything = len(b.visible)
    b.set_presence(INSTALLED)
    mine = len(b.visible)
    b.set_presence(MISSING)
    rest = len(b.visible)
    assert mine + rest == everything
    assert rest > 0
    assert not any(g.key in here for g in b.visible.games)


def test_the_three_answers_are_exclusive():
    b, _ = _installed_browser()
    b.set_presence(MISSING)
    assert not b.installed_only
    b.set_presence(INSTALLED)
    assert b.installed_only
    b.set_presence(ALL)
    assert not b.installed_only


def test_the_toggle_still_means_on_and_off():
    # It is what the grid's own key and the quick button use, and pressing it
    # from "not installed" should land somewhere obvious rather than cycling.
    b, _ = _installed_browser()
    b.set_presence(MISSING)
    b.toggle_installed()
    assert b.presence == INSTALLED
    b.toggle_installed()
    assert b.presence == ALL


def test_a_presence_nobody_has_heard_of_is_ignored():
    b, _ = _installed_browser()
    b.set_presence("perhaps")
    assert b.presence == ALL


def test_an_uninstall_updates_the_view_when_asking_for_what_is_missing():
    b, here = _installed_browser()
    b.set_presence(MISSING)
    before = len(b.visible)
    b.set_installed(set())
    assert len(b.visible) > before
