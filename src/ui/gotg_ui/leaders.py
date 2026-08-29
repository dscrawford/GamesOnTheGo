"""Where the binding labels go, and how a line reaches each button.

Geometry only, and no pygame, for the same reason layout.py has none: a label
two pixels into its neighbour, or a leader crossing the artwork, is wrong in a
way that only shows up on one window size, and that is the half worth holding
in a test.

The problem has a name — *boundary labeling* — and labels sit on a rail down
each side, each joined to its button by a straight leader.

Three steps: split the buttons by which half they sit in, stack the labels down
each rail in the order their buttons appear, then **untangle by swapping**.

The last step is the one that earns the guarantee, and it rests on a fact about
straight lines rather than on the ordering. If two leaders cross, giving each
the other's label always makes the pair strictly shorter — the triangle
inequality, applied to the two triangles the crossing makes. So swapping a
crossing pair removes that crossing and shortens the total, there are finitely
many assignments, and the process cannot loop. It stops crossing-free.

Sorting alone does *not* get you there, which is worth recording because it is
the obvious thing to believe. Sorted order is exactly optimal when every leader
costs the same horizontally — true for the orthogonal leaders the literature
usually draws, because all of them end at the same rail. A straight leader's
length mixes both axes, so the problem stops being one-dimensional and sorted
order can leave crossings behind. It is a good starting point and nothing more:
measured over 400 random arrangements it left 136 crossings, which the swap
pass then clears.

Straight leaders rather than the orthogonal kind for the same reason: with
several leaders turning in one band, the runs *out* of the buttons cut across
the runs *down* the band, and no ordering of those tracks fixes it — seven
orderings were measured and the best still left 200. Straight was both the
tidiest and the least tangled to begin with.

Simulated annealing is what the literature reaches for at larger sizes and it
would work, but it is stochastic: labels would settle differently between two
runs and jitter while the screen was open. For a dozen labels there is no
reason to accept that. docs/controller-diagram-research.md has the citations.
"""

from __future__ import annotations

from dataclasses import dataclass

# How far the rail sits outside the pad. A fraction of the diagram's width so
# the shape holds from a Deck to a television, like layout.py's grid.
GUTTER_FRACTION = 0.07

# Swapping strictly shortens the layout each time, so this cannot spin — it is
# a guard against a geometry bug, not an expected limit, and a layout that hits
# it is drawn anyway rather than refused.
MAX_UNTANGLE_PASSES = 200


@dataclass(frozen=True)
class Anchor:
    """One button: what it is called, where it is, and what it does."""

    input: str
    x: float
    y: float
    label: str


@dataclass(frozen=True)
class Placed:
    """One label, placed, with the leader that reaches it."""

    anchor: Anchor
    side: str
    x: float
    y: float
    points: tuple[tuple[float, float], ...]

    @property
    def align(self) -> str:
        """Which end of the text meets the gutter."""
        return "left" if self.side == "right" else "right"


def _side_of(anchor: Anchor, centre_x: float, pinned: dict[str, str]) -> str:
    """Which rail a button belongs to.

    Splitting on the midline is right for almost everything, and wrong for the
    shoulders: L and R sit near the top centre, so the midline would send them
    to whichever side they happen to fall on and run their leaders diagonally
    across the whole pad. They are named instead.
    """
    if anchor.input in pinned:
        return pinned[anchor.input]
    return "left" if anchor.x < centre_x else "right"


def spread(desired: list[float], height: float, top: float, bottom: float) -> list[float]:
    """Push overlapping labels apart, keeping the order they came in.

    `desired` is in ascending order and stays that way: every step below is a
    monotone shift, which is what preserves the no-crossings property the sort
    established. A pass that reordered would be free to produce a shorter
    layout and would tangle the leaders doing it.

    Four steps, each a fallback for the last: settle downwards, then borrow the
    slack at the top, then close the gaps, then overlap evenly rather than run
    off the screen. The last one is deliberate — a cramped diagram is readable
    and a label drawn outside the window is not.
    """
    if not desired:
        return []

    placed = list(desired)
    for index in range(1, len(placed)):
        overlap = (placed[index - 1] + height) - placed[index]
        if overlap > 0:
            placed[index] += overlap

    # Ran past the bottom? Take back whatever room is spare above the first.
    overflow = (placed[-1] + height) - bottom
    if overflow > 0:
        shift = min(overflow, placed[0] - top)
        if shift > 0:
            placed = [y - shift for y in placed]
            overflow -= shift

    # Still over: close the gaps between labels, largest first, down to flush.
    if overflow > 0:
        total_gap = sum(placed[i] - (placed[i - 1] + height) for i in range(1, len(placed)))
        if total_gap > 0:
            keep = max(0.0, 1 - overflow / total_gap)
            tightened = [placed[0]]
            for index in range(1, len(placed)):
                gap = placed[index] - (placed[index - 1] + height)
                tightened.append(tightened[-1] + height + gap * keep)
            placed = tightened
            overflow = (placed[-1] + height) - bottom

    # Nothing left to give: share the overlap out so no label leaves the band.
    if overflow > 0 and len(placed) > 1:
        step = (bottom - top - height) / (len(placed) - 1)
        placed = [top + index * step for index in range(len(placed))]
    elif overflow > 0:
        placed = [top]

    return placed


def place(
    anchors: list[Anchor],
    diagram: tuple[float, float, float, float],
    label_height: float,
    pinned: dict[str, str] | None = None,
) -> list[Placed]:
    """Lay every label out around the diagram, and route its leader.

    `diagram` is where the pad itself was drawn — (x, y, width, height) — and
    anchors carry absolute coordinates inside it. Labels go on rails outside
    that rectangle, in reading order down each side.
    """
    if not anchors:
        return []

    left, top, width, height = diagram
    right = left + width
    centre_x = left + width / 2
    gutter = width * GUTTER_FRACTION

    pinned = pinned or {}

    rails = {"left": left - gutter, "right": right + gutter}
    # The band labels may occupy. Taller than the pad, because a rail holding
    # seven labels needs more room than the pad is tall.
    band_top = top - height * 0.18
    band_bottom = top + height * 1.18

    out: list[Placed] = []
    for side in ("left", "right"):
        mine = [a for a in anchors if _side_of(a, centre_x, pinned) == side]
        if not mine:
            continue
        mine.sort(key=lambda a: (a.y, a.x))

        # The slots are fixed here and never move again: rest each label level
        # with its button, then push the overlaps apart. What untangling
        # changes is only *which* button gets which slot.
        wanted = [a.y - label_height / 2 for a in mine]
        slots = spread(wanted, label_height, band_top, band_bottom)

        rail_x = rails[side]
        for anchor, label_y in zip(mine, _untangle(mine, slots, rail_x, label_height), strict=True):
            middle = label_y + label_height / 2
            out.append(
                Placed(
                    anchor=anchor,
                    side=side,
                    x=rail_x,
                    y=label_y,
                    points=((anchor.x, anchor.y), (rail_x, middle)),
                )
            )

    return out


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Do two open segments meet anywhere but at an endpoint?

    Open on purpose: two leaders from buttons on the same row share a y and
    would otherwise read as crossing where they only run alongside.
    """
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    denominator = d1x * d2y - d1y * d2x
    if denominator == 0:
        return False
    t = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / denominator
    u = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / denominator
    return 0 < t < 1 and 0 < u < 1


def _untangle(anchors: list[Anchor], slots: list[float], rail_x: float, label_height: float) -> list[float]:
    """Give each button a slot, swapping until no two leaders cross.

    Starts from the order the buttons are in, which is right nearly all of the
    time, and repairs what is left. Each swap makes the crossing pair strictly
    shorter, so the total falls with every one and the loop cannot return to an
    assignment it has already had.
    """
    assigned = list(slots)
    for _ in range(MAX_UNTANGLE_PASSES):
        swapped = False
        for i in range(len(anchors)):
            for j in range(i + 1, len(anchors)):
                a = ((anchors[i].x, anchors[i].y), (rail_x, assigned[i] + label_height / 2))
                b = ((anchors[j].x, anchors[j].y), (rail_x, assigned[j] + label_height / 2))
                if _segments_cross(*a, *b):
                    assigned[i], assigned[j] = assigned[j], assigned[i]
                    swapped = True
        if not swapped:
            break
    return assigned


def crossings(placed: list[Placed]) -> int:
    """How many leaders cross each other. Zero, once untangling has run.

    Here so a test can assert it, and the assertion has to be able to fail —
    an earlier version of this drew every leader's turn at one shared x, which
    made a formal crossing geometrically impossible and the check therefore
    vacuous. It scored a deliberately tangled layout zero, which is how that
    was caught. Any change to the leader shape should be checked against a
    hand-tangled layout before its zero is believed.
    """
    total = 0
    for index, first in enumerate(placed):
        for second in placed[index + 1 :]:
            for a, b in zip(first.points, first.points[1:], strict=False):
                for c, d in zip(second.points, second.points[1:], strict=False):
                    if _segments_cross(a, b, c, d):
                        total += 1
    return total
