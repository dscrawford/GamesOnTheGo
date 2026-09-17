"""The filter panel — narrowing the library without a keyboard.

The point of it is reachability: every one of these was already possible, and
half of them only from a keyboard. So the tests are about a d-pad. Two
directions on every row, wrapping at both ends, and a way back to the whole
library from a filter somebody set three screens ago.
"""

from gotg_ui import filters
from gotg_ui.browser import ALL, Browser
from gotg_ui.catalog import Game, Library


def library():
    return Library([
        Game(id="usa.zelda", platform="n64", title="Zelda", handler="rom"),
        Game(id="jpn.mother", platform="snes", title="Mother 2", handler="rom"),
        Game(id="eur.mario", platform="n64", title="Mario", handler="rom"),
    ])


def browser():
    return Browser(library(), per_page=10)


# --- getting around ----------------------------------------------------------


def test_the_rows_are_in_the_order_somebody_reaches_for_them():
    # Search is slow to use and clear undoes the rest, so both are near the
    # end; the controller row is last because it leaves the panel entirely.
    assert filters.ROWS[0] == filters.PLATFORM
    assert filters.ROWS[-1] == filters.CONTROLLER
    assert filters.ROWS.index(filters.CLEAR) < filters.ROWS.index(filters.CONTROLLER)


def test_down_walks_the_rows():
    panel = filters.Filters()
    assert panel.row == filters.PLATFORM
    panel.move(1)
    assert panel.row == filters.REGION


def test_it_wraps_at_both_ends():
    # One press from the bottom back to the top beats walking back up it.
    panel = filters.Filters()
    panel.move(-1)
    assert panel.row == filters.ROWS[-1]
    panel.move(1)
    assert panel.row == filters.PLATFORM


# --- changing things ---------------------------------------------------------


def test_platform_goes_both_ways():
    # A pad could only ever cycle forwards, and there are a dozen platforms.
    ours = browser()
    panel = filters.Filters()
    panel.adjust(ours, 1)
    first = ours.platform
    panel.adjust(ours, -1)
    assert ours.platform == ALL
    assert first != ALL


def test_region_is_reachable_at_all():
    # It was on shift-Tab, which a controller has not got.
    ours = browser()
    panel = filters.Filters()
    panel.move(1)
    assert panel.row == filters.REGION
    panel.adjust(ours, 1)
    assert ours.region != ALL


def test_installed_only_answers_to_either_direction():
    # A toggle has no sides, and making left mean "off" would leave somebody
    # pressing right on a row that does not change.
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.INSTALLED))
    panel.adjust(ours, 1)
    assert ours.installed_only
    panel.adjust(ours, -1)
    assert not ours.installed_only


def test_left_and_right_still_nudge_without_opening_anything():
    # For when the answer is next door and a list is more than is wanted.
    ours = browser()
    panel = filters.Filters()
    panel.adjust(ours, 1)
    assert ours.platform != ALL
    assert not panel.open


def test_the_search_row_asks_for_the_keyboard():
    # The panel does not own the text box: on a Deck the Steam keyboard rises
    # over it, and that is the caller's business.
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.SEARCH))
    assert panel.press(ours) == filters.TYPING


# --- getting back ------------------------------------------------------------


def test_clear_undoes_every_filter_at_once():
    # The thing somebody wants when the grid has gone empty and they cannot
    # see which of four filters did it.
    ours = browser()
    ours.cycle_platform(1)
    ours.cycle_region(1)
    ours.set_search("zel")
    ours.toggle_installed()

    filters.clear(ours)
    assert ours.platform == ALL
    assert ours.region == ALL
    assert ours.search == ""
    assert not ours.installed_only


def test_clear_on_an_unfiltered_library_changes_nothing():
    ours = browser()
    filters.clear(ours)
    assert ours.platform == ALL
    assert ours.region == ALL
    assert not ours.installed_only


def test_the_clear_row_clears():
    ours = browser()
    ours.cycle_platform(1)
    panel = filters.Filters(index=filters.ROWS.index(filters.CLEAR))
    assert panel.press(ours) is None
    assert ours.platform == ALL


# --- what it says ------------------------------------------------------------


def test_a_row_reads_its_value_out_of_the_browser():
    # Not a second copy: a search typed on the grid is already right when the
    # panel opens.
    ours = browser()
    ours.set_search("mario")
    assert filters.value_of(ours, filters.SEARCH) == "mario"
    ours.set_search("")
    assert filters.value_of(ours, filters.SEARCH) == "—"


def test_every_row_has_a_label_and_a_value():
    ours = browser()
    panel = filters.Filters()
    rows = filters.rows_for(ours, panel)
    assert len(rows) == len(filters.ROWS)
    assert all(label for label, _, _ in rows)
    # Exactly one row is the selected one, whichever it is.
    assert sum(1 for _, _, selected in rows if selected) == 1


# --- the dropdown ------------------------------------------------------------


def test_pressing_a_row_opens_its_list():
    # Twelve platforms is a list to look down, not a value to press right
    # eleven times.
    ours = browser()
    panel = filters.Filters()
    panel.press(ours)
    assert panel.open
    assert panel.choice.row == filters.PLATFORM
    assert ALL in panel.choice.options


def test_the_list_opens_on_what_the_filter_already_is():
    # So the eye starts where the value is rather than at the top of a dozen.
    ours = browser()
    ours.set_platform("snes")
    panel = filters.Filters()
    panel.press(ours)
    assert panel.choice.value == "snes"


def test_choosing_sets_the_filter_and_closes_the_list():
    ours = browser()
    panel = filters.Filters()
    panel.press(ours)
    panel.choice.move(1)
    wanted = panel.choice.value
    panel.choose(ours)
    assert ours.platform == wanted
    assert not panel.open


def test_the_list_wraps_too():
    ours = browser()
    panel = filters.Filters()
    panel.press(ours)
    panel.choice.move(-1)
    assert panel.choice.value == panel.choice.options[-1]


def test_closing_the_list_changes_nothing():
    # B is one step back, not a way to set a filter by accident.
    ours = browser()
    panel = filters.Filters()
    panel.press(ours)
    panel.choice.move(2)
    panel.close()
    assert not panel.open
    assert ours.platform == ALL


def test_the_way_back_to_everything_is_the_top_of_the_list():
    ours = browser()
    panel = filters.Filters()
    panel.press(ours)
    assert panel.choice.options[0] == ALL


def test_regions_get_a_list_of_their_own():
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.REGION))
    panel.press(ours)
    assert panel.choice.row == filters.REGION
    assert "jpn" in panel.choice.options


def test_installed_is_a_list_of_three_rather_than_a_toggle():
    # Every row on the panel answers to the same press, and the third answer --
    # what is *not* here -- is the one a yes/no toggle could not ask for.
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.INSTALLED))
    panel.press(ours)
    assert panel.choice.options == ("all", "installed", "not installed")
    panel.choice.index = panel.choice.options.index("installed")
    panel.choose(ours)
    assert ours.installed_only


def test_the_row_can_ask_for_what_is_not_installed():
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.INSTALLED))
    panel.press(ours)
    panel.choice.index = panel.choice.options.index("not installed")
    panel.choose(ours)
    assert ours.presence == "not installed"
    assert not ours.installed_only


def test_choosing_what_it_already_is_leaves_it_there():
    # It used to be a toggle underneath, where choosing "yes" twice meant no.
    ours = browser()
    filters.set_value(ours, filters.INSTALLED, "installed")
    filters.set_value(ours, filters.INSTALLED, "installed")
    assert ours.installed_only


def test_nudging_the_row_goes_round_all_three_both_ways():
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.INSTALLED))
    panel.adjust(ours, 1)
    assert ours.presence == "installed"
    panel.adjust(ours, 1)
    assert ours.presence == "not installed"
    panel.adjust(ours, 1)
    assert ours.presence == "all"
    panel.adjust(ours, -1)
    assert ours.presence == "not installed"


def test_search_and_clear_have_no_list():
    ours = browser()
    for row in (filters.SEARCH, filters.CLEAR):
        assert filters.options_for(ours, row) == ()


# --- the way to a controller screen ------------------------------------------


def test_the_controller_row_offers_every_platform_but_not_all_of_them():
    # There is no diagram for "everything at once", so offering it would be
    # offering a screen that cannot be drawn.
    ours = browser()
    options = filters.options_for(ours, filters.CONTROLLER)
    assert "n64" in options
    assert ALL not in options


def test_choosing_a_platform_hands_it_back_rather_than_filtering_by_it():
    # It is a way in, not a filter: picking n64 here should open that
    # controller's screen, not narrow the grid to N64 games.
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.CONTROLLER))
    panel.press(ours)
    panel.choice.index = panel.choice.options.index("n64")
    assert panel.choose(ours) == "n64"
    assert ours.platform == ALL


def test_choosing_a_filter_hands_nothing_back():
    ours = browser()
    panel = filters.Filters()
    panel.press(ours)
    panel.choice.index = panel.choice.options.index("n64")
    assert panel.choose(ours) is None
    assert ours.platform == "n64"


def test_the_controller_row_does_not_nudge():
    # Left and right on a row that is a door would do nothing visible, so it
    # does nothing at all rather than half-opening something.
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.CONTROLLER))
    panel.adjust(ours, 1)
    assert not panel.open
    assert ours.platform == ALL


def test_closing_the_controller_list_opens_nothing():
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.CONTROLLER))
    panel.press(ours)
    panel.close()
    assert panel.choose(ours) is None


# --- the other view ----------------------------------------------------------


def test_the_view_row_offers_both_ways_of_looking():
    ours = browser()
    assert filters.options_for(ours, filters.VIEW) == ("grid", "rows")


def test_choosing_a_view_switches_it_and_re_pages():
    # The shelf holds more than the grid, so the page size changes with it.
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.VIEW))
    before = ours.per_page
    panel.press(ours)
    panel.choice.index = panel.choice.options.index("rows")
    assert panel.choose(ours) is None          # a view is a setting, not a door
    assert ours.view == "rows"
    assert ours.per_page > before


def test_the_view_row_nudges_round_both_ways():
    ours = browser()
    panel = filters.Filters(index=filters.ROWS.index(filters.VIEW))
    panel.adjust(ours, 1)
    assert ours.view == "rows"
    panel.adjust(ours, 1)
    assert ours.view == "grid"
    panel.adjust(ours, -1)
    assert ours.view == "rows"


def test_the_row_says_which_view_is_on():
    ours = browser()
    assert filters.value_of(ours, filters.VIEW) == "grid"
    ours.set_view("rows")
    assert filters.value_of(ours, filters.VIEW) == "rows"


def test_clearing_the_filters_leaves_the_view_alone():
    # Clear all is about what is shown, not how. Somebody who has chosen the
    # shelf has not asked to be put back in the grid.
    ours = browser()
    ours.set_view("rows")
    ours.set_platform("n64")
    filters.clear(ours)
    assert ours.platform == ALL
    assert ours.view == "rows"
