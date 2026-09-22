"""`config/controllers/` — what each controller is, as data rather than code.

Two kinds of test. The first is the loader: a missing directory, a broken file
and a platform nobody claims all have to be survivable, because the picker is
what tells you a controller is wrong and it cannot be the thing that breaks.

The second is agreement, and it is the reason these files are worth having.
Three descriptions of a GameCube pad exist -- padmap's layout, our config, and
the circles on the drawing -- and a screen that walks one while labelling
another points at the wrong button and says nothing about it. So the config is
checked against the artwork here, and against padmap's own layouts wherever
padmap is checked out beside this.
"""

import pathlib
import xml.etree.ElementTree as ET

import pytest

from gotg_ui import schemes

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "controllers"
ART = pathlib.Path(__file__).resolve().parents[2] / "src" / "ui" / "assets" / "controllers"


def anchors_of(artwork: str) -> set[str]:
    root = ET.parse(ART / f"{artwork}.svg").getroot()
    return {
        (el.get("id") or "")[len("anchor-"):]
        for el in root.iter()
        if (el.get("id") or "").startswith("anchor-")
    }


def all_schemes():
    return schemes.load(CONFIG)


# --- the loader --------------------------------------------------------------


def test_every_file_in_the_directory_loads():
    found = all_schemes()
    assert found, "no controller files found at all"
    assert "gamecube" in found and "generic" in found


def test_a_platform_is_claimed_by_exactly_one_controller():
    """Two files claiming one platform is a coin toss over which pad is drawn,
    decided by whichever sorts first."""
    seen: dict[str, str] = {}
    for scheme in all_schemes().values():
        for platform in scheme.platforms:
            assert platform not in seen, f"{platform} claimed by {seen.get(platform)} and {scheme.name}"
            seen[platform] = scheme.name


def test_a_platform_nobody_claims_gets_the_generic_pad(monkeypatch):
    monkeypatch.setenv("GOTG_CONFIG", str(CONFIG.parent))
    schemes.forget()
    assert schemes.for_platform("saturn").name == "generic"
    assert schemes.for_platform("").name == "generic"
    schemes.forget()


def test_a_missing_directory_is_not_a_crash(tmp_path):
    assert schemes.load(tmp_path / "nothing here") == {}


def test_a_broken_file_costs_only_that_controller(tmp_path):
    (tmp_path / "broken.yaml").write_text("controls: [this is not a mapping\n")
    (tmp_path / "fine.yaml").write_text("layout: snes\nplatforms: [snes]\ncontrols:\n  a: B\n")
    found = schemes.load(tmp_path)
    assert "broken" not in found
    assert found["fine"].controls == {"a": "B"}


def test_the_fallback_survives_a_config_that_is_not_there(tmp_path):
    assert schemes.load(tmp_path) == {}
    # No file, no controls, but still something to draw a screen from.
    assert schemes.Scheme(name="generic").layout == "generic"


# --- agreement with the drawings ---------------------------------------------


@pytest.mark.parametrize("name", sorted(p.stem for p in CONFIG.glob("*.yaml")))
def test_every_anchor_a_controller_names_exists_in_its_drawing(name):
    scheme = all_schemes()[name]
    drawn = anchors_of(scheme.artwork)
    for control, anchor in scheme.anchors.items():
        assert anchor in drawn, f"{name}: {control} points at {anchor!r}, which {scheme.artwork}.svg has not got"


@pytest.mark.parametrize("name", sorted(p.stem for p in CONFIG.glob("*.yaml")))
def test_an_anchor_is_only_named_when_it_differs_from_the_control(name):
    # `dpup: dpup` is noise, and noise in a table is where a typo hides.
    scheme = all_schemes()[name]
    for control, anchor in scheme.anchors.items():
        assert control != anchor, f"{name}: {control} maps to itself"


@pytest.mark.parametrize("name", sorted(p.stem for p in CONFIG.glob("*.yaml")))
def test_every_control_is_named(name):
    scheme = all_schemes()[name]
    assert scheme.controls, f"{name} describes no controls"
    for control, label in scheme.controls.items():
        assert label.strip(), f"{name}: {control} has no label"


def test_the_gamecube_drawing_marks_every_control():
    """The one drawn for this, so every control should have a circle. The
    others are older artwork and are allowed gaps -- an N64's Z is underneath
    the pad, and the drawing is from the front."""
    scheme = all_schemes()["gamecube"]
    assert set(scheme.controls) <= anchors_of("gamecube")


def test_the_drawings_mark_the_sticks_padmap_never_asks_about():
    """A stick is a reading, not a binding.

    padmap's gamecube, switch and wiiu layouts have no `leftstick_*` in them,
    so its capture never asks for the main stick and the scheme cannot list
    it -- `test_the_controls_are_padmaps_own` holds those two together. The
    clone forwards the axes regardless, so the ring on the drawing shows
    where the stick is whether or not anything ever bound it. That needs the
    artwork to mark it: docs/requests/the-analog-stick.md.
    """
    for artwork in ("gamecube", "generic"):
        drawn = anchors_of(artwork)
        for way in ("up", "down", "left", "right"):
            assert f"leftstick_{way}" in drawn, f"{artwork}.svg has no left stick to point at"
            assert f"rightstick_{way}" in drawn, f"{artwork}.svg has no right stick to point at"


# --- agreement with padmap ---------------------------------------------------

PADMAP_LAYOUTS = pathlib.Path.home() / "Documents/padmap/rust/crates/padmap-core/data/layouts"


@pytest.mark.skipif(not PADMAP_LAYOUTS.is_dir(), reason="padmap is not checked out beside this")
@pytest.mark.parametrize("name", sorted(p.stem for p in CONFIG.glob("*.yaml")))
def test_the_controls_are_padmaps_own(name):
    """The capture walks padmap's layout and the screen labels these. A control
    in one and not the other is a step with no label, or a label for a button
    the capture never asks for."""
    import json

    scheme = all_schemes()[name]
    layout = json.loads((PADMAP_LAYOUTS / f"{scheme.layout}.json").read_text())
    assert set(scheme.controls) == {c["canonical"] for c in layout["controls"]}
    for control in layout["controls"]:
        assert scheme.controls[control["canonical"]] == control["label"]


def test_the_sticks_that_sit_in_an_octagon_say_so():
    """A ring is a picture of the gate, so it has to be the right shape.

    An N64's stick and a GameCube's two sit in octagonal gates -- eight
    corners you can feel through the thumb -- and an Xbox pad's are round.
    Drawing a circle over all of them drew a controller nobody owns.
    """
    schemes = all_schemes()
    assert schemes["gamecube"].gate("left") == "octagon"
    assert schemes["gamecube"].gate("right") == "octagon", "the C-stick has a gate too"
    assert schemes["n64"].gate("left") == "octagon"
    # An N64's C group is four buttons and no gate at all. The ring standing
    # in for it is round, because eight corners there would be invented.
    assert schemes["n64"].gate("right") == "circle"
    # Everything else is round until somebody says otherwise, which is what a
    # pad with no stick at all wants too.
    assert schemes["switch"].gate("left") == "circle"
    assert schemes["snes"].gate("right") == "circle"
