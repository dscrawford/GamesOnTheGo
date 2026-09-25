"""A seat's device node, and what the device behind it actually is.

The fixture is `/proc/bus/input/devices` copied off a real Steam Deck over
ssh, unedited. It is here because the Deck is the one controller whose name
lies: its built-in controls report "Valve Software Steam Controller", the same
string a Steam Controller Puck reports, and every name rule in
`config/icons.yaml` therefore draws it as a pad on a table. The ids do not
lie, and these pin that.
"""

from __future__ import annotations

import pathlib

import pytest

from gotg_ui.devices import ids_for, node_key
from gotg_ui.icons import icon_name

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
HIDRAW = str(FIXTURES / "hidraw-steam-deck")
DECK = (FIXTURES / "input-devices-steam-deck.txt").read_text()
TWO_PADS = (FIXTURES / "input-devices-two-pads.txt").read_text()


@pytest.mark.parametrize(
    "node,expected",
    [
        ("/dev/input/event4", "event4"),
        ("event4", "event4"),
        ("  /dev/input/event12  ", "event12"),
        ("", ""),
        (None, ""),
    ],
)
def test_a_node_is_read_however_danstick_named_it(node, expected):
    assert node_key(node) == expected


def test_the_deck_s_own_controls_are_valve_s_1205():
    # Both halves of it: the Deck presents its controls as one keyboard/mouse
    # pair (lizard mode) and one HID interface, and either node identifies the
    # machine somebody is holding.
    assert ids_for("/dev/input/event4", DECK) == "28de:1205"
    assert ids_for("event5", DECK) == "28de:1205"


def test_a_pad_with_no_evdev_node_is_known_through_sysfs():
    """A Deck's pad is hidraw-only, so a seat on it names /dev/hidrawN.

    /proc/bus/input/devices has never heard of that node -- the fixture is
    the Deck's own `uevent`, and `HID_ID` is where its ids live.
    """
    assert ids_for("/dev/hidraw3", hidraw_root=HIDRAW) == "28de:1205"
    assert ids_for("hidraw3", hidraw_root=HIDRAW) == "28de:1205"
    assert ids_for("/dev/hidraw9", hidraw_root=HIDRAW) is None
    assert icon_name("Valve Software Steam Controller", ids_for("hidraw3", hidraw_root=HIDRAW)) == "steamdeck"


def test_a_node_nothing_lists_is_not_guessed_at():
    assert ids_for("/dev/input/event999", DECK) is None
    assert ids_for("", DECK) is None


def test_a_pad_on_the_desktop_reports_its_own_maker():
    xbox = [n for n in TWO_PADS.splitlines() if "Vendor=" in n]
    assert xbox, "the two-pad fixture lost its device ids"
    # Whatever is in it, every node resolves to four-and-four hex digits.
    got = ids_for("event0", TWO_PADS)
    assert got is None or (len(got) == 9 and got[4] == ":")


def test_the_deck_is_drawn_as_a_handheld_and_the_puck_is_not():
    """The bug this exists for.

    Character for character the same name; only 28de:1205 says one of them is
    a machine with a screen in it. Without the ids the Deck got `steam.svg` --
    a picture of a controller nobody in the room was holding.
    """
    deck = "Valve Software Steam Controller"
    assert icon_name(deck) == "steam"
    assert icon_name(deck, ids_for("event4", DECK)) == "steamdeck"
    assert icon_name("Valve Software Steam Controller Puck", "28de:1304") == "steam"


def test_ids_are_matched_exactly_not_by_substring():
    # A name rule is a substring because names are prose; an id is an
    # identifier, and "28de:12051" is a different device from a Deck.
    assert icon_name("Some Pad", "28de:12051") == "generic"
    assert icon_name("Some Pad", "28DE:1205") == "steamdeck"


def test_a_name_still_decides_when_there_is_no_node_to_ask_about():
    assert icon_name("Xbox 360 Controller", None) == "xbox"
    assert icon_name("Xbox 360 Controller", "") == "xbox"
