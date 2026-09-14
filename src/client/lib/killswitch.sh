# shellcheck shell=bash
# The controller's way out of a running game — the combination, and why a game
# needs one, are in gotg-killswitch/main.c. This is the wiring: start the
# watcher before every launch, and never let its absence stop a game.

# How long the combo is held before the game stops. Overridable for somebody
# who wants it harder or easier to reach, not because anything here needs it.
GOTG_KILLSWITCH_HOLD_MS="${GOTG_KILLSWITCH_HOLD_MS:-3000}"

# Digits or the default, checked before the value is used for anything.
#
# Two reasons, and the second is the real one. A value the watcher cannot parse
# is a watcher that exits immediately, which is a kill switch that quietly is
# not there. And $(( )) evaluates array subscripts, which run command
# substitution — so an environment variable reaching arithmetic unchecked is
# not a tuning knob, it is a way to run commands.
killswitch_hold_ms() {
  local want="${GOTG_KILLSWITCH_HOLD_MS:-3000}"
  if [[ ! "$want" =~ ^[0-9]{1,9}$ ]] || ((10#$want < 1)); then
    warn "GOTG_KILLSWITCH_HOLD_MS is not a number of milliseconds; holding 3000"
    want=3000
  fi
  printf '%s' "$want"
}

killswitch_bin() {
  if [[ -n "${GOTG_KILLSWITCH_BIN:-}" ]]; then
    printf '%s' "$GOTG_KILLSWITCH_BIN"
    return 0
  fi
  command -v gotg-killswitch 2>/dev/null
}

# Start the watcher for a process that is about to become the game.
#
# Called before the exec that turns this shell into the emulator, so the pid it
# is given is the pid the emulator will have — the same process, after exec,
# which is also its process group's leader under both Steam and a terminal.
killswitch_start() {
  local pid="$1" bin hold
  [[ "${GOTG_KILLSWITCH:-1}" != "0" ]] || return 0
  hold="$(killswitch_hold_ms)"

  bin="$(killswitch_bin)" || bin=""
  if [[ -z "$bin" || ! -x "$bin" ]]; then
    warn "no gotg-killswitch here; the controller cannot stop this game"
    return 0
  fi

  # A background process rather than a job to manage: this shell is about to
  # be replaced by the emulator, and the watcher outlives that untouched — it
  # polls the pid and exits on its own when the game is gone. stderr is
  # inherited, which under Steam is the per-game log, the one place somebody
  # looks to find out why a session ended.
  "$bin" --pid "$pid" --hold-ms "$hold" &
  log "${C_DIM}hold L + R and Start for $((hold / 1000))s to stop the game${C_RESET}"
}
