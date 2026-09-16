# shellcheck shell=bash
# Running a game under padmap, which is what the controllers actually are.
#
# padmap republishes each physical pad through uinput as a controller whose
# identity it made -- stable across replugs, in the player order somebody chose
# by holding a button -- and captures the mapping for models SDL does not know.
# Two things have to happen for a game to see any of that:
#
#   - the daemon has to be running, or there are no virtual pads to see;
#   - the mapping has to be in the environment *before* the emulator starts,
#     because SDL reads its database once, at startup. A mapping written a
#     second later does nothing until the next launch, which is the failure
#     that looks like "it works the second time".
#
# Neither is allowed to stop a launch. A machine with no daemon, or one where
# padmap cannot reach uinput, still plays games -- with whatever SDL already
# knows, which is what it had before padmap existed.

# Start the daemon if it is not running, or restart it if it is running code
# older than what is installed.
#
# Once per process: PADMAP_SKIP_DAEMON_CHECK is padmap's own flag for exactly
# this, and the picker sets it before launching a game so a launch from the
# grid does not ask again.
# Overridable, like every other tool this shells out to: a test needs a
# stand-in that records how it was called, and the packaged client puts the
# real one first on PATH where a test's copy cannot win.
padmap_bin() { printf '%s' "${GOTG_PADMAP:-padmap}"; }
padmap_rs_bin() { printf '%s' "${GOTG_PADMAP_RS:-padmap-rs}"; }

padmap_ensure() {
  [[ "${PADMAP_SKIP_DAEMON_CHECK:-0}" != "1" ]] || return 0
  command -v "$(padmap_bin)" >/dev/null 2>&1 || return 0
  if ! "$(padmap_bin)" ensure-daemon >/dev/null 2>&1; then
    warn "padmap has no current daemon; controllers will be whatever SDL finds"
  fi
  export PADMAP_SKIP_DAEMON_CHECK=1
}

# Launch, with padmap's mappings in the environment.
#
# `padmap-rs exec` reads the file the daemon wrote at its last republish and
# puts it in SDL_GAMECONTROLLERCONFIG. That is the whole of what an emulator
# reading no controller database needs -- Cemu is the reason it exists -- and
# it costs nothing for the ones that read one.
#
# Execs, so the process the kill switch is holding stays the one it was told
# about: padmap-rs replaces itself with the game.
padmap_exec() {
  padmap_ensure
  if command -v "$(padmap_rs_bin)" >/dev/null 2>&1; then
    exec "$(padmap_rs_bin)" exec -- "$@"
  fi
  exec "$@"
}
