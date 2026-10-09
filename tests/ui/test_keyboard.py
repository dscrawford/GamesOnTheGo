"""Property under test: a search can be typed, and left, from a d-pad alone.

The box took only key events, and a pad has none: X opened it and nothing a
controller could do closed it. The keys are walked with directions, A types
the one under the cursor, and the way out is the key at the bottom right.
"""

from __future__ import annotations

from gotg_ui.keyboard import BACKSPACE, CLEAR, CLOSE, ROWS, SPACE, Keyboard


def test_the_way_out_is_the_bottom_right_key():
    assert ROWS[-1][-1] == CLOSE
    # Up from the digits wraps to the bottom row, and left from its start to its end.
    assert Keyboard(row=0).move(-1, -1).closes


def test_a_press_types_the_key_under_the_cursor():
    board = Keyboard().press().move(1, 0).press()
    assert board.text == "qw"


def test_directions_wrap_both_ways():
    board = Keyboard()
    assert board.move(-1, 0).key == "p"
    assert board.move(0, -1).key == "1"
    assert board.move(0, len(ROWS) - 1).key == "1"
    assert board.move(0, -2).key == SPACE


def test_down_from_the_right_hand_letters_lands_on_close_not_space():
    # Rows of ten over a row of four: the cursor keeps its place along the
    # row, not its index, or a thumb at the corner has to cross to find Close.
    board = Keyboard().move(-1, 0)  # p
    assert board.move(0, 3).key == CLOSE
    assert Keyboard().move(0, 3).key == SPACE


def test_the_verbs_along_the_bottom():
    board = Keyboard(text="ab")
    bottom = len(ROWS) - 1
    assert board.at(bottom, ROWS[bottom].index(SPACE)).press().text == "ab "
    assert board.at(bottom, ROWS[bottom].index(BACKSPACE)).press().text == "a"
    assert board.at(bottom, ROWS[bottom].index(CLEAR)).press().text == ""


def test_close_changes_nothing_so_what_was_on_the_grid_stays():
    board = Keyboard(text="zel").at(len(ROWS) - 1, ROWS[-1].index(CLOSE))
    assert board.closes
    assert board.press() is board


def test_a_real_keyboard_types_into_the_same_text():
    assert Keyboard(text="a").with_text("ab").text == "ab"
    assert Keyboard(text="ab").backspace().text == "a"
    assert Keyboard().typed("x").typed(" ").text == "x "


def test_a_pointer_off_the_keys_moves_nothing():
    board = Keyboard()
    assert board.at(99, 0) is board
    assert board.at(0, 99) is board
    assert board.at(2, 3).key == "f"


def test_it_is_a_value():
    board = Keyboard()
    board.press()
    board.move(1, 1)
    assert board == Keyboard()
