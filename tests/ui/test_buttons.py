"""Which button was pressed, whatever pad it was pressed on.

The bug this exists for: every screen compared `event.button` to a number, and
the numbers were an Xbox pad's. On the Steam Controller this was built beside,
the shoulders are 9 and 10 and Start is 6 — so the bumpers turned no pages and
Start opened nothing, while A and B happened to line up and hid it.

No pygame here: the numbering is SDL's and is written down, so it can be
checked without a screen. `pads.py` asserts at import that SDL still agrees.
"""

from gotg_ui import buttons


def test_the_shoulders_are_named_not_numbered():
    # The whole bug: 9 and 10 on a Steam Controller, 4 and 5 on an Xbox pad.
    assert buttons.name_for(9, standard=True) == buttons.LB
    assert buttons.name_for(10, standard=True) == buttons.RB
    assert buttons.name_for(4, standard=False) == buttons.LB
    assert buttons.name_for(5, standard=False) == buttons.RB


def test_start_is_six_on_a_pad_sdl_knows_and_seven_on_one_it_does_not():
    # Which is why Start opened nothing: the code only ever looked for 7.
    assert buttons.name_for(6, standard=True) == buttons.START
    assert buttons.name_for(7, standard=False) == buttons.START


def test_the_face_buttons_are_the_ones_that_always_worked():
    # A and B are 0 and 1 either way, which is exactly why this hid for so long.
    for index, name in ((0, buttons.A), (1, buttons.B), (2, buttons.X), (3, buttons.Y)):
        assert buttons.name_for(index, standard=True) == name
        assert buttons.name_for(index, standard=False) == name


def test_an_index_nothing_maps_is_not_a_button():
    assert buttons.name_for(19, standard=True) is None
    assert buttons.name_for(19, standard=False) is None


# --- the d-pad, from either direction ----------------------------------------


def test_a_hat_points_the_way_the_grid_counts():
    # SDL's hat is y-up; every screen here is y-down.
    assert buttons.hat_step((0, 1)) == (0, -1)
    assert buttons.hat_step((0, -1)) == (0, 1)
    assert buttons.hat_step((-1, 0)) == (-1, 0)


def test_a_diagonal_survives():
    assert buttons.hat_step((1, 1)) == (1, -1)


def test_the_centre_is_not_a_direction():
    # It is the release, and stepping on it would move twice for one press.
    assert buttons.hat_step((0, 0)) is None


def test_a_dpad_button_points_the_same_way_as_the_hat():
    # The controller API reports a d-pad as four buttons and the joystick one
    # as a hat; a screen should not have to know which it got.
    assert buttons.step_for(buttons.name_for(11, standard=True)) == (0, -1)
    assert buttons.step_for(buttons.name_for(14, standard=True)) == (1, 0)


def test_a_face_button_points_nowhere():
    assert buttons.step_for(buttons.A) is None
    assert buttons.step_for(None) is None
