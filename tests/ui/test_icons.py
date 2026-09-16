"""Which drawing a controller's name asks for.

Names are what a maker wrote, not an identifier, so this is substring matching
and the order of the rules is load-bearing. These pin the cases where a
reasonable rule would pick the wrong picture.
"""

from __future__ import annotations

import pytest

from gotg_ui.icons import FALLBACK, icon_name, icon_path


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Xbox 360 Controller", "xbox"),
        ("Microsoft X-Box 360 pad", "xbox"),
        ("Steam Controller", "steam"),
        ("Valve Software Steam Controller Puck", "steam"),
        ("Nintendo Switch Pro Controller", "switch"),
        ("Joy-Con (L)", "switch"),
        ("Wii Remote", "wii"),
        ("Nintendo GameCube Controller", "gamecube"),
        ("PLAYSTATION(R)3 Controller", "playstation"),
        ("Sony DualSense Wireless Controller", "playstation"),
        ("N64 Adapter", "n64"),
        ("Sega Genesis pad", "megadrive"),
    ],
)
def test_a_name_picks_its_picture(name, expected):
    assert icon_name(name) == expected


def test_steam_s_virtual_pad_is_drawn_as_the_xbox_pad_it_pretends_to_be():
    # Steam publishes 28de:11ff named "Microsoft X-Box 360 pad" for every
    # controller it drives. Drawing a Steam Controller there would be a picture
    # of the wrong hardware whenever somebody is holding anything else.
    assert icon_name("Steam Virtual Gamepad") == "xbox"


def test_an_unknown_pad_still_gets_a_pad():
    assert icon_name("Some Adapter 9000") == FALLBACK
    assert icon_name("") == FALLBACK
    assert icon_name(None) == FALLBACK


def test_every_rule_resolves_to_a_file_that_exists():
    # A rule naming artwork nobody vendored draws the generic pad instead, and
    # silently -- so the rules and the directory are checked against each other
    # here rather than by somebody noticing a wrong picture.
    from gotg_ui.icons import _rules, controllers_dir, icons_dir

    for _needle, icon in _rules():
        found = (icons_dir() / f"{icon}.svg").exists() or (
            controllers_dir() / f"{icon}.svg"
        ).exists()
        assert found, f"no artwork for {icon!r}"


def test_the_path_falls_back_to_the_console_diagrams():
    # n64 has no icon of its own: it is drawn once, next door, with anchors the
    # strip ignores.
    path = icon_path("N64 Adapter")
    assert path is not None
    assert path.name == "n64.svg"
    assert path.parent.name == "controllers"


def test_an_icon_of_its_own_wins():
    path = icon_path("Steam Controller")
    assert path is not None
    assert path.parent.name == "icons"


def test_a_keyboard_is_a_keyboard():
    from gotg_ui.icons import icon_name

    assert icon_name("AT Translated Set 2 keyboard") == "keyboard"
    assert icon_name("Logitech USB Keyboard and Mouse") == "keyboard-mouse"
    assert icon_name("Razer Mouse") == "mouse"
