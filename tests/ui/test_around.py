"""Moving around a controller with a d-pad.

Tested against the real GameCube anchors, because the question this answers is
geometric and a made-up grid would not ask it: A, B, X and Y sit in a lopsided
diamond, the C-stick is four points around one circle, and Z overlaps R.
"""

import pathlib
import xml.etree.ElementTree as ET

import pytest

from gotg_ui.around import first, nearest

ART = pathlib.Path(__file__).resolve().parents[2] / "src" / "ui" / "assets" / "controllers"


def anchors_of(name):
    root = ET.parse(ART / f"{name}.svg").getroot()
    box = [float(v) for v in root.get("viewBox").split()]
    out = {}
    for el in root.iter():
        ident = el.get("id") or ""
        if ident.startswith("anchor-"):
            x = (float(el.get("cx")) - box[0]) / box[2]
            y = (float(el.get("cy")) - box[1]) / box[3]
            out[ident[len("anchor-"):]] = (x, y)
    return out


@pytest.fixture(scope="module")
def gamecube():
    return anchors_of("gamecube")


RIGHT, LEFT, UP, DOWN = (1, 0), (-1, 0), (0, -1), (0, 1)


def test_right_from_the_dpad_reaches_the_other_half_of_the_pad(gamecube):
    # Not "whatever is next in the file", which on this pad is dpdown.
    assert nearest(gamecube, "dpleft", RIGHT) == "dpright"


def test_the_dpad_walks_as_a_dpad(gamecube):
    assert nearest(gamecube, "dpup", DOWN) in ("dpleft", "dpright", "dpdown")
    assert nearest(gamecube, "dpleft", RIGHT) == "dpright"
    assert nearest(gamecube, "dpright", LEFT) == "dpleft"


def test_the_c_stick_walks_as_a_stick(gamecube):
    assert nearest(gamecube, "rightstick_left", RIGHT) == "rightstick_right"
    assert nearest(gamecube, "rightstick_up", DOWN) in ("rightstick_left", "rightstick_right", "rightstick_down")


def test_the_face_buttons_go_the_way_they_are_drawn(gamecube):
    # Y is above A and X is to its right, which is the GameCube's own diamond.
    assert nearest(gamecube, "a", UP) == "y"
    assert nearest(gamecube, "a", RIGHT) == "x"


def test_the_edge_of_the_pad_is_the_edge(gamecube):
    # A picture of a physical object: pressing right at the right-hand edge
    # should do what it does on the thing itself, which is nothing.
    rightmost = max(gamecube.items(), key=lambda kv: kv[1][0])[0]
    assert nearest(gamecube, rightmost, RIGHT) == rightmost


def test_nothing_moves_without_a_direction(gamecube):
    assert nearest(gamecube, "a", (0, 0)) == "a"


def test_a_control_that_is_not_on_the_drawing_stays_put(gamecube):
    assert nearest(gamecube, "nonesuch", RIGHT) == "nonesuch"


def test_an_empty_drawing_is_survivable():
    assert nearest({}, "a", RIGHT) == "a"
    assert first({}) == ""


def test_it_starts_somewhere_definite(gamecube):
    # The same control every time, and one an eye would land on.
    assert first(gamecube) == first(gamecube)
    top = min(y for _x, y in gamecube.values())
    assert gamecube[first(gamecube)][1] == pytest.approx(top, abs=0.02)


def test_a_press_always_lands_on_a_real_control(gamecube):
    # Whatever it returns is something the screen can highlight.
    for control in gamecube:
        for step in (RIGHT, LEFT, UP, DOWN):
            assert nearest(gamecube, control, step) in gamecube


def test_every_control_is_reachable_from_every_other(gamecube):
    """A button nothing can walk to is a button nobody can bind.

    Breadth-first over the four directions: if the graph is not connected, some
    control is unreachable and the screen has a dead corner.
    """
    start = first(gamecube)
    seen, queue = {start}, [start]
    while queue:
        control = queue.pop()
        for step in (RIGHT, LEFT, UP, DOWN):
            found = nearest(gamecube, control, step)
            if found not in seen:
                seen.add(found)
                queue.append(found)
    assert seen == set(gamecube), f"unreachable: {sorted(set(gamecube) - seen)}"
