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

# Which controller the overlay draws and walks for this game, and so which
# walk danstick drives the clones from: the platform's console, except for an
# environment whose game reads a modern pad itself -- a PC port asking for the
# `xbox360` identity. DK64's recompilation reads A, B, X, Y, both bumpers, both
# triggers and Back off an Xbox pad; told "n64", danstick drove the clone from
# the N64 walk, which has no X, Y, right trigger or Select, and the game lost a
# third of its controls along with the rebind chord. Such a game is played with
# the generic pad, whose walk (or, with none, the pad's own layout) is the one
# that fits it.
killswitch_console() {
  local attr="$1" platform="$2"
  if [[ -n "$attr" && "$(danstick_identity "$attr")" == xbox360* ]]; then
    printf 'generic'
    return 0
  fi
  printf '%s' "$platform"
}

# Put this launch, and everything it starts from here on, in a cgroup of its
# own -- gotg-game-<pid>.scope under the user's systemd -- so the kill switch
# stops the game by its scope (contain.rs) and nothing it forks outlives it.
# Called after the watcher, the daemon and the log's tee have started, which
# stay outside. Never fatal: without it the kill switch walks the tree.
game_contain() {
  local pid="$1" title="${2:-}" busctl unit cg
  [[ "${GOTG_CONTAIN:-1}" != "0" ]] || return 0
  busctl="${GOTG_BUSCTL:-busctl}"
  command -v "$busctl" >/dev/null 2>&1 || return 0
  [[ -r "/proc/$pid/cgroup" ]] || return 0
  unit="gotg-game-$pid.scope"
  if ! "$busctl" --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 \
    org.freedesktop.systemd1.Manager StartTransientUnit 'ssa(sv)a(sa(sv))' \
    "$unit" fail 3 PIDs au 1 "$pid" CollectMode s inactive-or-failed \
    Description s "GOTG: ${title:-a game}" 0 >/dev/null 2>&1; then
    warn "could not put the game in a scope of its own; the controller stops its process tree instead"
    return 0
  fi
  # The move is a job: the game must not start until it has landed.
  for _ in $(seq 1 50); do
    cg="$(<"/proc/$pid/cgroup")"
    [[ "$cg" != *"/$unit" ]] || return 0
    sleep 0.02
  done
  warn "the game's scope ($unit) did not take this launch in time"
}

# Start the watcher for a process that is about to become the game.
#
# Called before the exec that turns this shell into the emulator, so the pid it
# is given is the pid the emulator will have — the same process, after exec,
# which is also its process group's leader under both Steam and a terminal.
killswitch_start() {
  local pid="$1" platform="${2:-}" attr="${3:-}" bin hold
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
  # The hold draws itself over the game. Somewhere with no display, or with a
  # compositor that will not float a window over a fullscreen one, can turn
  # that off and keep the switch.
  local -a picture=()
  [[ "${GOTG_KILLSWITCH_OVERLAY:-1}" != "0" ]] || picture=(--no-overlay)

  # Which controller the rebind chord draws, and which danstick layout it walks.
  local -a console=()
  [[ ! "$platform" =~ ^[A-Za-z0-9_-]+$ ]] || console=(--platform "$platform")

  # Whose saves go up when the menu's Exit stops the game, and the client
  # that sends them -- this one, by the path it runs from.
  local -a saves=()
  [[ -z "$attr" || -z "${GOTG_ROOT:-}" ]] || saves=(--saves "$attr" --client "$GOTG_ROOT/bin/gotg")

  "$bin" --pid "$pid" --hold-ms "$hold" ${console[@]+"${console[@]}"} ${picture[@]+"${picture[@]}"} \
    ${saves[@]+"${saves[@]}"} &
  log "${C_DIM}hold L + R and Select for half a second for the menu; L + R and Start for $((hold / 1000))s stops the game${C_RESET}"
}
