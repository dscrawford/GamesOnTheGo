"""An input from the pad, read back through danstick's profile: which control.

The profile below is the one danstick wrote for the user's Xbox Wireless
Controller, trimmed. Every value in it was measured by danstick.
"""

from __future__ import annotations

from gotg_ui.pressing import controls_for, controls_on

BUTTONS = {
    "a": {"index": 0, "kind": "button", "value": 0},
    "b": {"index": 1, "kind": "button", "value": 0},
    "dpup": {"index": 0, "kind": "hat", "value": 1},
    "dpdown": {"index": 0, "kind": "hat", "value": 4},
    "dpleft": {"index": 0, "kind": "hat", "value": 8},
    "dpright": {"index": 0, "kind": "hat", "value": 2},
    "leftshoulder": {"index": 2, "kind": "axis", "value": 1},
    "rightstick_left": {"index": 3, "kind": "axis", "value": -1},
    "rightstick_right": {"index": 3, "kind": "axis", "value": 1},
}


def test_a_button_is_the_control_bound_to_its_index():
    assert controls_for(BUTTONS, "button", 0, 1) == ["a"]
    assert controls_for(BUTTONS, "button", 1, 1) == ["b"]
    assert controls_for(BUTTONS, "button", 7, 1) == []


def test_a_hat_reading_is_its_directions_and_a_diagonal_is_two():
    assert controls_for(BUTTONS, "hat", 0, 1) == ["dpup"]
    assert controls_for(BUTTONS, "hat", 0, 1 | 2) == ["dpright", "dpup"]
    assert controls_for(BUTTONS, "hat", 0, 0) == []


def test_an_axis_is_pressed_past_half_and_in_its_bound_direction():
    assert controls_for(BUTTONS, "axis", 2, 0.9) == ["leftshoulder"]
    assert controls_for(BUTTONS, "axis", 2, 0.2) == []
    assert controls_for(BUTTONS, "axis", 3, -0.8) == ["rightstick_left"]
    assert controls_for(BUTTONS, "axis", 3, 0.8) == ["rightstick_right"]


def test_a_profile_with_nothing_in_it_names_nothing():
    assert controls_for({}, "button", 0, 1) == []
    assert controls_for(None, "button", 0, 1) == []


def test_every_control_an_input_could_be():
    """`controls_on` is how a dot is taken away again.

    An axis crossing back through the middle is not a press, and the only way
    to know which dot to remove is to ask what that axis is bound to -- the
    threshold question `controls_for` answers cannot.
    """
    buttons = {
        "leftstick_left": {"kind": "axis", "index": 0, "value": -1},
        "leftstick_right": {"kind": "axis", "index": 0, "value": 1},
        "a": {"kind": "button", "index": 0, "value": 0},
    }
    assert controls_on(buttons, "axis", 0) == ["leftstick_left", "leftstick_right"]
    assert controls_on(buttons, "button", 0) == ["a"]
    assert controls_on(buttons, "button", 9) == []
    assert controls_on({}, "axis", 0) == []
