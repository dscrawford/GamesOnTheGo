
import os as _os
from collections.abc import Mapping as _Mapping

# What Steam leaves in the environment of anything it starts that would stop
# the picker hearing padmap. Steam hands every launch the controllers Steam
# Input handles, to be ignored in favour of its virtual gamepad -- and padmap's
# clone of a Steam Controller wears the Steam Controller's own ids. On a Deck
# the pad paired, the seat was drawn, and then nothing pressed moved anything:
# the picker's SDL was ignoring the one device it was there to listen to.
# `gotg play` has undone this for games since Ryujinx waited on the same clone
# (padmap_clear_steam_env); the picker never goes through it.
_STEAM_LEFTOVERS = ("SDL_GAMECONTROLLER_IGNORE_DEVICES", "SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT")


def steam_leftovers(environ: _Mapping[str, str]) -> tuple[str, ...]:
    """The variables Steam set here that the picker must not inherit."""
    return tuple(name for name in _STEAM_LEFTOVERS if name in environ)


# Before pygame initialises anything, which is why it lives here: the picker
# and the launch gate both import this package before SDL starts.
for _name in steam_leftovers(_os.environ):
    del _os.environ[_name]

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
