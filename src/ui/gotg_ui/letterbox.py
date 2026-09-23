"""Where a fixed-size frame sits in a window of any size, and back again.

The layout is worked out once at 1280x800; the window is whatever the panel
is. These are the two directions between them: the frame into the window,
largest that fits and centred, and a pointer in the window back onto the
frame, which is what the grid hit-tests against. No pygame, so a test can
hold them.
"""

from __future__ import annotations


def fit(inner: tuple[int, int], outer: tuple[int, int]) -> tuple[int, int, int, int]:
    """Where a frame of size `inner` goes in a window of size `outer`: as large
    as fits, centred, with bars where the shapes disagree."""
    iw, ih = max(1, inner[0]), max(1, inner[1])
    ow, oh = max(1, outer[0]), max(1, outer[1])
    scale = min(ow / iw, oh / ih)
    width, height = round(iw * scale), round(ih * scale)
    return ((ow - width) // 2, (oh - height) // 2, width, height)


def to_frame(pos: tuple[float, float], box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int]:
    """A window position, as a position on the frame. Clamped to its edges, so
    a click in a letterbox bar lands on the nearest thing rather than off it."""
    x, y, width, height = box
    fx = (pos[0] - x) * size[0] / max(1, width)
    fy = (pos[1] - y) * size[1] / max(1, height)
    return (int(min(max(fx, 0), size[0] - 1)), int(min(max(fy, 0), size[1] - 1)))
