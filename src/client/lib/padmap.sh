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

# The name of the launch-time controller check. Overridable for the same reason
# every other tool here is: a test needs a stand-in it can watch being called.
padmap_seat_bin() { printf '%s' "${GOTG_SEAT:-gotg-seat}"; }

# Ask about controllers before the game takes the screen.
#
# Only ever asks when there is something to ask -- no controller seated, or one
# that has never been mapped for this console -- and the check itself is a
# socket round trip that costs nothing on a machine somebody has already set
# up. The reason it is here rather than in the picker is that a game can be
# started from a terminal, from Steam, or from the grid, and the controller is
# missing in exactly the same way in all three.
#
# Absent is fine. gotg-seat ships with the picker, which is a separate package
# and deliberately not something the client depends on: found on PATH it runs,
# and not found it is skipped without a word. Failure is fine too -- it exits 0
# by design, and this ignores its status anyway, because nothing about a
# controller is a reason not to start a game somebody asked for.
padmap_seat_gate() {
  local platform="$1" title="${2:-}" seat
  [[ "${GOTG_SEAT_GATE:-1}" != "0" ]] || return 0
  seat="$(padmap_seat_bin)"
  command -v "$seat" >/dev/null 2>&1 || return 0
  padmap_ensure
  "$seat" --platform "$platform" --title "$title" || true
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
