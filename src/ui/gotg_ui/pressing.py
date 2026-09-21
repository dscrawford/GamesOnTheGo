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
