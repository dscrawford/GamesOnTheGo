
import os as _os

# SDL's HIDAPI backend claims Valve's ids and then hides padmap's evdev clone
# of a Steam Controller from the joystick list -- the raw pad was refused,
# the seat was taken, and the clone that was supposed to drive the picker
# never reached it. Off, as the launcher already sets it for every game:
# padmap owns the hidraw device, and its clone is the one SDL should see.
# Before pygame initialises anything, which is why it lives here.
_os.environ.setdefault("SDL_JOYSTICK_HIDAPI", "0")
