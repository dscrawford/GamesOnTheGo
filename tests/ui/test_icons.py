"""Which drawing a controller's name asks for.

Names are what a maker wrote, not an identifier, so this is substring matching
and the order of the rules is load-bearing. These pin the cases where a
reasonable rule would pick the wrong picture.
"""

from __future__ import annotations

import json

import pytest

from gotg_ui.icons import FALLBACK, icon_image, icon_name, icon_path


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
    # A handheld has no controller to draw, so there is no strip icon for it:
    # it is drawn once, next door, with anchors the strip ignores.
    path = icon_path("Game Boy Advance")
    assert path is not None
    assert path.name == "gba.svg"
    assert path.parent.name == "controllers"


def test_an_icon_of_its_own_wins():
    path = icon_path("Steam Controller")
    assert path is not None
    assert path.parent.name == "icons"


def test_the_keyboard_seat_is_a_keyboard_and_mouse():
    # danstick's seat name and the space bar's hold; see config/icons.yaml.
    from gotg_ui.icons import icon_name

    assert icon_name("Keyboard") == "keyboard-mouse"
    assert icon_name("keyboard") == "keyboard-mouse"
    assert icon_name("Logitech USB Keyboard and Mouse") == "keyboard-mouse"
    assert icon_name("Razer Mouse") == "mouse"


# --- the built PNGs the strip actually draws ----------------------------------
#
# The SVGs are never loaded at runtime: they are rasterised at build time, like
# the console diagrams next to them, and the strip loads a PNG. Until this the
# whole icon path was unreachable -- the package installs `assets/built`, and
# `assets/icons` was not in it.


def built(tmp_path, *names):
    """A stand-in for what build-controllers.py --icons writes."""
    out = tmp_path / "icons"
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        (out / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (out / "icons.json").write_text(
        json.dumps({"version": 1, "height": 96, "icons": {n: f"{n}.png" for n in names}})
    )
    return tmp_path


def test_a_known_pad_draws_its_own_icon(tmp_path, monkeypatch):
    monkeypatch.setenv("GOTG_UI_ASSETS", str(built(tmp_path, "xbox", "generic")))
    assert icon_image("Xbox 360 Controller").name == "xbox.png"


def test_an_unknown_pad_draws_the_generic_one(tmp_path, monkeypatch):
    # The whole point of the fallback: an unrecognised controller is still a
    # controller, and a drawing of a pad says so where its name does not.
    monkeypatch.setenv("GOTG_UI_ASSETS", str(built(tmp_path, "xbox", "generic")))
    assert icon_image("Some Pad Nobody Has Heard Of").name == "generic.png"


def test_a_pad_whose_icon_was_not_built_still_gets_the_generic_one(tmp_path, monkeypatch):
    monkeypatch.setenv("GOTG_UI_ASSETS", str(built(tmp_path, "generic")))
    assert icon_image("Nintendo Switch Pro Controller").name == "generic.png"


def test_no_built_icons_at_all_is_no_icon_rather_than_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("GOTG_UI_ASSETS", str(tmp_path))
    assert icon_image("Xbox 360 Controller") is None


def test_every_rule_has_a_drawing_to_build_from():
    # Each rule's icon must exist as an SVG somewhere, or the build produces
    # no PNG for it and the strip quietly falls back for ever.
    from gotg_ui.icons import _rules, controllers_dir, icons_dir

    for _, icon in _rules():
        assert (icons_dir() / f"{icon}.svg").exists() or (controllers_dir() / f"{icon}.svg").exists(), icon


def test_the_fallback_has_an_icon_of_its_own_without_anchors():
    # The console diagram called `generic` carries anchor circles for the
    # binding screen; at 28 pixels those are speckle. The strip needs its own.
    from gotg_ui.icons import icons_dir

    assert (icons_dir() / "generic.svg").exists()
    assert "anchor-" not in (icons_dir() / "generic.svg").read_text()


def test_a_deck_is_drawn_as_the_handheld_it_is():
    # Its controls are built into the thing in your hands, so the Steam
    # Controller's drawing is the wrong shape to recognise at a glance.
    from gotg_ui.icons import icon_name

    assert icon_name("Steam Deck") == "steamdeck"
    assert icon_name("Valve Software Steam Deck Controller") == "steamdeck"


def test_a_puck_is_still_a_steam_controller():
    from gotg_ui.icons import icon_name

    assert icon_name("Valve Software Steam Controller Puck") == "steam"
    assert icon_name("Steam Controller") == "steam"
