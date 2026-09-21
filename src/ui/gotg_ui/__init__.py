
import os as _os

# SDL's HIDAPI backend claims Valve's ids and then hides padmap's evdev clone
# of a Steam Controller from the joystick list -- the raw pad was refused,
# the seat was taken, and the clone that was supposed to drive the picker
# never reached it. Off, as the launcher already sets it for every game:
# padmap owns the hidraw device, and its clone is the one SDL should see.
# Before pygame initialises anything, which is why it lives here.
_os.environ.setdefault("SDL_JOYSTICK_HIDAPI", "0")

# padmap's own SDL mappings for the pads it has published, so a clone SDL
# knows nothing about -- a Steam Controller's -- opens as a gamepad with
# padmap's button names rather than as a bare joystick in an Xbox pad's
# order. Read by SDL when the controller subsystem starts, which the
# picker and the gate both do after this import. A game gets the same
# lines from `padmap-rs exec`.
_mappings = _os.path.join(
    _os.environ.get("XDG_CONFIG_HOME") or _os.path.expanduser("~/.config"), "padmap", "sdl_controllers.txt"
)
if _os.path.exists(_mappings):
    _os.environ.setdefault("SDL_GAMECONTROLLERCONFIG_FILE", _mappings)
