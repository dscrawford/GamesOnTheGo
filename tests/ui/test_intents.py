"""Property under test: what a key or a pad press *means* to a screen, with no
pygame -- Back and Confirm are the same words on every screen, a pad nobody
seated means nothing, and the keyboard before its seat is heard only as the
space bar that asks for one."""

from __future__ import annotations

import pytest

from gotg_ui import intents
from gotg_ui.intents import BACK, MOVE, NONE, OK, SPACE, Key, intent, keyboard_heard

A, B = "a", "b"


@pytest.mark.parametrize("key", [Key.ESCAPE, Key.B])
def test_escape_and_b_are_back(key):
    assert intent(key=key) == BACK


@pytest.mark.parametrize("key", [Key.RETURN, Key.KP_ENTER])
def test_return_and_keypad_enter_are_ok(key):
    assert intent(key=key) == OK


def test_the_pad_buttons_are_the_same_words():
    assert intent(button=B) == BACK
    assert intent(button=A) == OK


def test_space_is_its_own_word_and_a_screen_decides_what_it_confirms():
    # Space confirms in the menu and the filter panel and nowhere else, so it
    # must not be folded into OK here.
    assert intent(key=Key.SPACE) == SPACE
    assert not intent(key=Key.SPACE).confirms()
    assert intent(key=Key.SPACE).confirms(space=True)
    assert intent(key=Key.RETURN).confirms()
    assert not BACK.confirms(space=True)


@pytest.mark.parametrize(
    "key,step",
    [(Key.UP, (0, -1)), (Key.DOWN, (0, 1)), (Key.LEFT, (-1, 0)), (Key.RIGHT, (1, 0))],
)
def test_arrows_are_moves(key, step):
    assert intent(key=key) == MOVE(step)


def test_a_pad_direction_is_a_move_and_wins_over_a_button():
    assert intent(step=(0, 1)) == MOVE((0, 1))
    assert intent(step=(1, 0), button=A) == MOVE((1, 0))


def test_a_move_names_its_axes():
    m = intent(key=Key.LEFT)
    assert (m.dx, m.dy) == (-1, 0)
    assert (NONE.dx, NONE.dy) == (0, 0)


def test_a_pad_nobody_seated_means_nothing():
    # pads.button / pads.direction return None for a pad danstick has not
    # published, so that is all this is ever handed.
    assert intent(key=None, button=None, step=None) == NONE
    assert intent() == NONE


def test_other_buttons_and_keys_mean_nothing():
    assert intent(button="x") == NONE
    assert intent(button="start") == NONE
    assert intent(key=ord("z")) == NONE


def test_the_keycodes_are_the_ones_sdl_sends():
    # Written down so this module needs no pygame; app.py asserts the same
    # numbers against pygame when it is imported.
    assert (Key.ESCAPE, Key.B, Key.RETURN, Key.SPACE) == (27, 98, 13, 32)
    assert (Key.KP_ENTER, Key.UP, Key.DOWN, Key.LEFT, Key.RIGHT) == (
        1073741912,
        1073741906,
        1073741905,
        1073741904,
        1073741903,
    )


def test_before_a_seat_only_the_space_bar_is_heard():
    assert keyboard_heard(Key.SPACE, drives=False)
    for key in (Key.RETURN, Key.ESCAPE, Key.B, Key.UP, Key.DOWN, Key.KP_ENTER, ord("q"), None):
        assert not keyboard_heard(key, drives=False)


def test_with_a_seat_every_key_is_heard():
    for key in (Key.SPACE, Key.RETURN, Key.ESCAPE, ord("q"), None):
        assert keyboard_heard(key, drives=True)


def test_no_module_here_needs_pygame():
    assert "pygame" not in vars(intents)
