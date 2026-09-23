"""The frame in the window, and the pointer back on the frame.

The renderer draws a 1280x800 frame into a window of any size, and SDL reports
the pointer in the window's own pixels. Without the way back, a click on a 4K
panel landed on the tile at a third of the distance.
"""

from __future__ import annotations

from gotg_ui.letterbox import fit, to_frame


def test_a_window_the_frame_s_own_size_is_the_whole_window():
    assert fit((1280, 800), (1280, 800)) == (0, 0, 1280, 800)


def test_a_wider_panel_gets_bars_at_the_sides():
    # 3840x2160 is 16:9 and the frame is 16:10: height-bound, bars left and right.
    x, y, width, height = fit((1280, 800), (3840, 2160))
    assert (y, height) == (0, 2160)
    assert width == 3456 and x == (3840 - 3456) // 2


def test_a_taller_one_gets_bars_top_and_bottom():
    x, y, width, height = fit((1280, 800), (1280, 1024))
    assert (x, width) == (0, 1280)
    assert height == 800 and y == 112


def test_a_click_on_a_4k_panel_lands_where_it_was_aimed():
    box = fit((1280, 800), (3840, 2160))
    # The centre of the window is the centre of the frame.
    assert to_frame((1920, 1080), box, (1280, 800)) == (640, 400)
    # And a point a quarter of the way across the drawn frame is a quarter of
    # the way across the frame.
    x, y, width, height = box
    assert to_frame((x + width / 4, y + height / 4), box, (1280, 800)) == (320, 200)


def test_a_click_in_the_bar_lands_on_the_nearest_edge_not_off_it():
    box = fit((1280, 800), (3840, 2160))
    assert to_frame((0, 1080), box, (1280, 800)) == (0, 400)
    assert to_frame((3839, 1080), box, (1280, 800)) == (1279, 400)
