"""Which control a raw input is, on a pad padmap has profiled.

padmap's profile for a controller says, per control, what the pad does when
that control is used: button 3, hat 0 up, axis 2 positive. This is the same
table read the other way -- an input arriving from the pad, which control it
is -- so a screen can ring the button somebody is pressing while they look
for it. No pygame: the input is three plain values.
"""

from __future__ import annotations

# A hat's reading is a mask, and a diagonal is two directions at once.
HAT_BITS = {1: "up", 2: "right", 4: "down", 8: "left"}

# Past here an axis is pressed, not resting. Triggers rest at one end and
# travel one way; sticks rest in the middle. Both cross this on purpose only.
AXIS_ON = 0.5


# `buttons.py`'s short names for SDL's standard layout, as the element names
# the ares table binds against. The two vocabularies exist because one is for
# a person reading a screen and the other is SDL's; this is the seam.
STANDARD_ELEMENTS = {
    "a": "a",
    "b": "b",
    "x": "x",
    "y": "y",
    "back": "back",
    "start": "start",
    "lb": "leftshoulder",
    "rb": "rightshoulder",
    "up": "dpup",
    "down": "dpdown",
    "left": "dpleft",
    "right": "dpright",
}

# SDL's game-controller axis order, which is the same on every pad it maps,
# and what each end of each axis is called. The triggers travel one way and
# have one name.
STANDARD_AXES = {
    0: ("leftx-", "leftx+"),
    1: ("lefty-", "lefty+"),
    2: ("rightx-", "rightx+"),
    3: ("righty-", "righty+"),
    4: ("lefttrigger", "lefttrigger"),
    5: ("righttrigger", "righttrigger"),
}


def element_for_button(name: str | None) -> str | None:
    """What a standard button press is, in the table's words."""
    return STANDARD_ELEMENTS.get(str(name or ""))


def element_on_axis(index: int, value) -> str | None:
    """Which way this standard axis is pushed, or None for at rest."""
    ends = STANDARD_AXES.get(index)
    if ends is None or not isinstance(value, (int, float)):
        return None
    if value <= -AXIS_ON:
        return ends[0]
    if value >= AXIS_ON:
        return ends[1]
    return None


def elements_on_axis(index: int) -> list[str]:
    """Both ends of this standard axis -- what a return to the middle clears."""
    ends = STANDARD_AXES.get(index)
    return sorted(set(ends)) if ends else []


def controls_on(buttons: dict, kind: str, index: int) -> list[str]:
    """Every control bound to this input, whatever it currently reads.

    The other half of `controls_for`: that one answers "what is pressed", and
    this one "what could this input have been". A screen showing what is under
    each thumb needs both -- an axis back in the middle is not a press, and
    the only way to know which dot to take away is to know what that axis is
    bound to.
    """
    return sorted(
        control
        for control, binding in (buttons or {}).items()
        if isinstance(binding, dict) and binding.get("kind") == kind and binding.get("index") == index
    )


def controls_for(buttons: dict, kind: str, index: int, value) -> list[str]:
    """The controls this input is bound to, on this profile. Usually one."""
    out = []
    for control, binding in (buttons or {}).items():
        if not isinstance(binding, dict) or binding.get("kind") != kind or binding.get("index") != index:
            continue
        if kind == "button":
            out.append(control)
        elif kind == "hat":
            mask = binding.get("value")
            if isinstance(mask, int) and isinstance(value, int) and value & mask:
                out.append(control)
        elif kind == "axis":
            sign = binding.get("value")
            if isinstance(sign, int) and isinstance(value, (int, float)):
                if (sign >= 0 and value >= AXIS_ON) or (sign < 0 and value <= -AXIS_ON):
                    out.append(control)
    return sorted(out)
