"""Talking to SDL about pads: opening them, and reading their events.

The names and the numbering are in `buttons.py`, which holds no pygame so it
can be tested without a screen. This is the half that cannot be: it opens what
SDL found and turns its events into those names.
"""

from __future__ import annotations

import pygame

from .buttons import (
    BACK,
    DOWN,
    LB,
    LEFT,
    RB,
    RIGHT,
    STANDARD,
    START,
    UP,
    A,
    B,
    X,
    Y,
    hat_step,
    name_for,
    step_for,
)
from .clones import Owners

__all__ = [
    "A", "B", "BACK", "DOWN", "LB", "LEFT", "RB", "RIGHT", "START", "UP", "X", "Y",
    "Pads", "button", "direction", "init",
]

# SDL's own constants, checked against the numbers buttons.py writes out. If a
# future SDL renumbers its layout, this is where it is noticed -- at import,
# rather than as a bumper that silently stops turning pages.
assert STANDARD[pygame.CONTROLLER_BUTTON_LEFTSHOULDER] == LB
assert STANDARD[pygame.CONTROLLER_BUTTON_RIGHTSHOULDER] == RB
assert STANDARD[pygame.CONTROLLER_BUTTON_START] == START
assert STANDARD[pygame.CONTROLLER_BUTTON_A] == A


# Every pad opened as a Controller, by instance id.
#
# SDL sends a press twice for one of these -- once as CONTROLLERBUTTONDOWN with
# its own numbering, once as JOYBUTTONDOWN with the hardware's -- and reading
# both would act twice on one press, under two different names. The raw one is
# dropped for any pad SDL has a mapping for, and kept for the pads where it is
# all there is.
_mapped: set[int] = set()

# What each open pad is called, and its GUID, by SDL instance id. A raw pad
# reaches this program whenever padmap is not holding it -- a failed grab, a
# Steam Controller it cannot grab, or its own seating mode, which grabs
# nothing on purpose -- and acting on those presses is the picker taking
# orders from a controller nobody has assigned. See `clones.py`: the rule
# has no off switch but GOTG_ANY_PAD.
_owners = Owners()


def _allowed(event) -> bool:
    """Whether this event came from a pad padmap published.

    `instance_id` on everything SDL2 sends, and `joy` for the older spelling,
    so a pygame that answers only the second is not a picker that answers
    nothing. An id this has never opened is nobody's, and is refused.
    """
    instance = getattr(event, "instance_id", None)
    if instance is None:
        instance = getattr(event, "joy", None)
    return _owners.may_drive(instance)


def button(event) -> str | None:
    """The name of the button this event is, or None if it is not one."""
    if not _allowed(event):
        return None
    if event.type == pygame.CONTROLLERBUTTONDOWN:
        return name_for(event.button, standard=True)
    if event.type == pygame.JOYBUTTONDOWN:
        if getattr(event, "instance_id", None) in _mapped:
            return None
        return name_for(event.button, standard=False)
    return None


def direction(event) -> tuple[int, int] | None:
    """Which way this event points, or None.

    A d-pad arrives as four buttons through the controller API and as a hat
    through the joystick one; a screen should not have to know which it got.
    """
    if event.type == pygame.JOYHATMOTION:
        if not _allowed(event):
            return None
        # A mapped pad's d-pad arrives as buttons as well; taking the hat too
        # would step twice.
        if getattr(event, "instance_id", None) in _mapped:
            return None
        return hat_step(event.value)
    return step_for(button(event))


class Pads:
    """Every controller SDL can see, held open.

    Held because closing one stops its events, and opened as Controllers
    because that is what turns a pad's own numbering into SDL's. A pad SDL has
    no mapping for is opened as a joystick instead: fewer promises, but its
    buttons still arrive.
    """

    def __init__(self) -> None:
        self._open: dict[int, object] = {}

    def add(self, index: int) -> None:
        """Open whatever SDL has just found at this device index."""
        from pygame._sdl2 import controller

        try:
            if controller.is_controller(index):
                pad = controller.Controller(index)
                stick = pad.as_joystick()
                instance = stick.get_instance_id()
                self._open[instance] = pad
                _mapped.add(instance)
            else:
                stick = pygame.joystick.Joystick(index)
                instance = stick.get_instance_id()
                self._open[instance] = stick
            # The name and the GUID, because SDL will rename a clone that
            # mirrors a pad it recognises and only the GUID still says what the
            # device was really called. phys is not asked for: SDL does not
            # answer it, and padmap sets it best-effort anyway.
            _owners.opened(instance, stick.get_name() or "", stick.get_guid() or "")
        except (pygame.error, AttributeError):
            # A pad can go away between being announced and being opened, and
            # a picker that raised there would die of somebody unplugging one.
            return

    def remove(self, instance_id: int) -> None:
        self._open.pop(instance_id, None)
        _mapped.discard(instance_id)
        _owners.closed(instance_id)

    def open_all(self) -> None:
        for index in range(pygame.joystick.get_count()):
            self.add(index)

    def __len__(self) -> int:
        return len(self._open)


def init() -> Pads:
    """Start the controller subsystem and open what is already plugged in."""
    from pygame._sdl2 import controller

    pygame.joystick.init()
    controller.init()
    pads = Pads()
    pads.open_all()
    return pads
