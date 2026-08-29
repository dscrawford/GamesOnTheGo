"""The label layout: that it never crosses, never overlaps, never leaves.

The three properties the algorithm claims. All of them are the kind that hold
on the window you tried and break on the one you did not, so they are asserted
over a spread of sizes and arrangements rather than a single fixture.
"""

from __future__ import annotations

import pytest

from gotg_ui.leaders import Anchor, Placed, crossings, place, spread

RECT = (400.0, 200.0, 480.0, 288.0)


def pad_anchors(rect=RECT) -> list[Anchor]:
    """A plausible gamepad's worth: both sides, a cluster, two shoulders."""
    left, top, width, height = rect
    uv = {
        "Up": (0.29, 0.42),
        "Down": (0.29, 0.61),
        "Left": (0.24, 0.51),
        "Right": (0.35, 0.51),
        "A": (0.77, 0.51),
        "B": (0.71, 0.61),
        "Y": (0.65, 0.51),
        "X": (0.71, 0.42),
        "L": (0.28, 0.21),
        "R": (0.72, 0.21),
        "Select": (0.45, 0.43),
        "Start": (0.55, 0.43),
    }
    return [
        Anchor(input=name, x=left + u * width, y=top + v * height, label=f"{name} — something")
        for name, (u, v) in uv.items()
    ]


def test_every_anchor_is_placed_exactly_once():
    placed = place(pad_anchors(), RECT, 20.0)
    assert sorted(p.anchor.input for p in placed) == sorted(a.input for a in pad_anchors())


def test_no_leader_crosses_another():
    assert crossings(place(pad_anchors(), RECT, 20.0)) == 0


@pytest.mark.parametrize("label_height", [8.0, 16.0, 24.0, 40.0, 64.0])
def test_no_leader_crosses_at_any_label_size(label_height):
    """A taller label pushes harder, which is when the order-preserving spread
    earns its keep — a pass that reordered would tangle here and nowhere else."""
    assert crossings(place(pad_anchors(), RECT, label_height)) == 0


@pytest.mark.parametrize("size", [(1280, 800), (1920, 1080), (3840, 2160), (800, 480)])
def test_no_leader_crosses_at_any_window_size(size):
    width, height = size
    pad = (width * 0.3, height * 0.25, width * 0.4, height * 0.4)
    assert crossings(place(pad_anchors(pad), pad, height * 0.028)) == 0


def test_labels_on_one_side_never_overlap():
    label_height = 22.0
    placed = place(pad_anchors(), RECT, label_height)
    for side in ("left", "right"):
        ys = sorted(p.y for p in placed if p.side == side)
        gaps = [b - a for a, b in zip(ys, ys[1:], strict=False)]
        assert all(gap >= label_height - 1e-6 for gap in gaps), gaps


def test_the_shoulders_are_pinned_apart():
    """L and R sit near the top centre, so a midline split would put them on
    whichever side they fell on. Pinning is what stops a leader running the
    width of the pad."""
    placed = {p.anchor.input: p.side for p in place(pad_anchors(), RECT, 20.0, {"L": "left", "R": "right"})}
    assert placed["L"] == "left"
    assert placed["R"] == "right"


def test_a_leader_starts_on_its_button_and_ends_on_its_label():
    """Two points, and both of them the right ones. A leader that started
    anywhere but the button is the failure a screenshot would not show."""
    for item in place(pad_anchors(), RECT, 20.0):
        assert item.points[0] == (item.anchor.x, item.anchor.y)
        assert item.points[-1] == (item.x, item.y + 10.0)


def test_a_deliberately_tangled_layout_is_reported_as_tangled():
    """The guard on the guard. An earlier leader shape turned every leader at
    one shared x, which made a formal crossing geometrically impossible — so
    crossings() scored a hand-tangled layout zero and every no-crossing test
    above passed without testing anything. This is what catches that."""
    left, top, width, _ = RECT
    rail = left - width * 0.07
    anchors = [Anchor(input=f"b{i}", x=left + 40, y=top + i * 60, label="x") for i in range(4)]
    tangled = [
        Placed(anchor=a, side="left", x=rail, y=y, points=((a.x, a.y), (rail, y)))
        for a, y in zip(anchors, [top + 180, top + 120, top + 60, top], strict=True)
    ]
    assert crossings(tangled) > 0


def test_a_crowded_rail_still_fits_inside_its_band():
    """Twelve labels down one side of a short window: they have to overlap,
    and overlapping is the right answer — a label drawn off-screen is not."""
    rect = (100.0, 40.0, 200.0, 120.0)
    anchors = [Anchor(input=f"b{i}", x=rect[0] + 10, y=rect[1] + i * 3, label=f"b{i}") for i in range(12)]
    placed = place(anchors, rect, 30.0)
    top = rect[1] - rect[3] * 0.18
    bottom = rect[1] + rect[3] * 1.18
    assert all(top - 1e-6 <= p.y and p.y + 30.0 <= bottom + 1e-6 for p in placed)


def test_nothing_in_nothing_out():
    assert place([], RECT, 20.0) == []
    assert spread([], 10.0, 0.0, 100.0) == []


def test_spread_keeps_the_order_it_was_given():
    out = spread([50.0, 52.0, 54.0, 56.0], 20.0, 0.0, 400.0)
    assert out == sorted(out)
    assert all(b - a >= 20.0 - 1e-9 for a, b in zip(out, out[1:], strict=False))


def test_spread_leaves_a_layout_that_already_fits_alone():
    wanted = [0.0, 40.0, 80.0]
    assert spread(wanted, 20.0, -50.0, 200.0) == wanted


def test_random_arrangements_all_come_out_untangled():
    """The property, over arrangements nobody would think to write down.

    Seeded, so a failure is reproducible rather than a story about a build that
    went red once. Twenty labels and a 120px font are past anything the screen
    will ask for, which is the point — the guarantee should not depend on the
    input being reasonable.
    """
    import random

    rng = random.Random(7)
    for _ in range(300):
        count = rng.randint(2, 20)
        anchors = [
            Anchor(
                input=f"b{i}",
                x=RECT[0] + rng.uniform(0.05, 0.95) * RECT[2],
                y=RECT[1] + rng.uniform(0.05, 0.95) * RECT[3],
                label="x",
            )
            for i in range(count)
        ]
        height = rng.choice([8.0, 20.0, 48.0, 80.0, 120.0])
        assert crossings(place(anchors, RECT, height)) == 0


def test_buttons_stacked_on_one_spot_do_not_tangle():
    """Ten anchors at one point: every leader starts in the same place, so the
    only thing keeping them apart is the slot they were given."""
    anchors = [Anchor(input=f"b{i}", x=500.0, y=300.0, label="x") for i in range(10)]
    assert crossings(place(anchors, RECT, 24.0)) == 0
