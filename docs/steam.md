# Steam, and what it does not tell you

Every item here cost a debugging session. They are written down with the
symptom first, because that is how each one arrives.

## The shortcut file is Steam's, and only between runs

**Symptom:** you add a game, Steam never shows it — or it shows it once and
then it is gone.

`shortcuts.vdf` is read **once, at startup**, and rewritten from Steam's own
memory **when it exits**. So a shortcut written while Steam is running is
thrown away the moment it closes, and one written while it is closed is
invisible until it starts again. There is no supported way to add a non-Steam
shortcut to a running client.

What GOTG does: `gotg steam add|remove|picker` detect a running Steam and
queue the change (`gotg steam pending`), applying it at the next `gotg steam`
command with Steam closed. `install.sh` offers to close Steam, add, and start
it again — `steam -shutdown` is the way to ask it to quit, and it takes a few
seconds, so wait for the process to go before touching the file.

## Steam's environment is not a shell's

**Symptom:** the library entry opens and closes instantly, with nothing on
screen to say why.

A shortcut is run with Steam's environment, which carries **none of Nix's
profile directories**. `exec gotg-ui` therefore exits 127 before anything is
drawn. Worse, on some machines the inherited `PATH` is sparse enough that
`mkdir` and `date` are not on it either — a launcher that shells out to
coreutils to set up its own log dies before it can log the reason.

What GOTG does: the launcher `gotg steam picker` writes searches for the
picker — by name first, so a `nix profile upgrade` that moves the store path
is still followed, then `~/.nix-profile/bin`, `~/.local/state/nix/profile/bin`
and `/nix/var/nix/profiles/default/bin`. Its timestamp comes from bash rather
than `date(1)`, and the log is best effort. Nothing may stand between Steam
and the picker starting.

## Point Steam at a stable path, never at the store

A `/nix/store/...` path is correct exactly until the next upgrade, after which
Steam holds a path to a build that has been garbage collected. The shortcut
names `~/.local/state/gotg/launchers/<name>.sh`, which does not move, and the
launcher resolves what to run each time it is pressed.

## Game Mode has nowhere to print

**Symptom:** a launch fails and there is no error anywhere.

Game Mode is gamescope with one application on screen. There is no terminal,
and a dialog box may never be composited. Anything worth reading has to reach
a file: GOTG writes `~/.local/state/gotg/logs/<id>.log` per game and
`gotg-ui.log` for the picker, and `GOTG_NO_DIALOG=1` is the one switch that
stops a zenity nobody can see from standing in the way.

## SteamOS has no `/run/opengl-driver`

**Symptom:** "No RDP rendering support", "Window framebuffer support not
available", "Failed to create allocator", or a QA capture of one still frame.

Nix-built programs find the GPU userspace at `/run/opengl-driver` on NixOS.
SteamOS has no such path, and its own mesa is unloadable from our glibc. Every
loader needs pointing at nixpkgs' mesa: GL, EGL, **GBM** (a nested compositor
allocates through it) and Vulkan. See `src/client/env/foreign-gl.nix`, which
the emulator environments, the picker and the QA tools all use.

Under Game Mode the surface is X11 through gamescope, not Wayland — a nested
compositor must be able to fall back to the X11 backend, and a program that
needs a GL renderer for its window (the picker does, under Wayland) fails
without the above.

## Controllers: Steam is not the only one holding them

Steam's own input layer will happily present a virtual pad of its own, and a
Steam Controller is reachable through `/dev/hidraw*` with no evdev node at
all. A game that opens "whatever SDL finds" can end up on a raw pad while
padmap has that seat pointed somewhere else. `padmap-rs exec` starts a game
with only padmap's clones visible, hidraw included; see
[docs/controllers.md](controllers.md).

## Testing without the hardware

`gotg qa <id> --machine deck` reproduces the conditions above on a desktop —
no host GL, X11 only, a C locale, the Deck's own `xrandr` answer. Set
`GOTG_QA_HOST_DECK` and a run goes to the real Deck when it answers and stands
in only when it does not, saying which either way. Each difference found only
on hardware is added to that profile, so the next run without it finds the
same thing.
