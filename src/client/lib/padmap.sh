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
  # A moment before the daemon is asked after: anything it publishes from
  # here on is newer than this file, and anything older is a leftover.
  local marker said
  marker="$(mktemp)"
  # Unseated, and for as long as this shell -- and the emulator it execs
  # into -- lives. padmap treats the pair as a session name: a daemon already
  # following this pid, which is the picker's after its execvp, is left
  # alone; one belonging to no session, or another, is replaced.
  if ! said="$("$(padmap_bin)" ensure-daemon --fresh --follow "$$" 2>&1)"; then
    warn "padmap has no current daemon; controllers will be whatever SDL finds"
    rm -f "$marker"
    # Not latched: this failure is worth asking about again. The latch used
    # to be set on the way out regardless, so one bad start at the picker
    # disabled the check for every game launched from it afterwards.
    return 0
  fi
  # Only a daemon this call started has a first publish to wait for. One
  # that was already current published long ago, or has nothing to publish
  # yet because nobody is seated, and waiting on it would be waiting on a
  # button press.
  if [[ "$said" == *"daemon up"* ]]; then
    padmap_wait_published "$marker"
  fi
  rm -f "$marker"
  export PADMAP_SKIP_DAEMON_CHECK=1
}

# ensure-daemon returns once the daemon is *running*, which is before it has
# published anything. A launch on a machine where nothing had started it yet
# went straight on to `padmap-rs exec`, which read the mappings file a moment
# before it existed:
#
#     no mappings at /run/user/1000/padmap/env.sh (No such file or directory)
#     ... will see whatever SDL already knows
#
# and the game came up with no controller. A few seconds of waiting covers
# it; a daemon that never publishes gets the same warning it always did.
#
# "Published" means published by *this* daemon: the file has to be newer than
# the marker made before the daemon was started. A daemon that died in an
# earlier session leaves its file behind, and an existence test passed on
# that leftover while the new daemon had said nothing yet.
padmap_wait_published() {
  local marker="$1" file waited=0 limit="${GOTG_PADMAP_PUBLISH_WAIT:-30}"
  file="$(padmap_runtime_dir)/env.sh"
  while [[ ! "$file" -nt "$marker" ]] && ((waited < limit)); do
    sleep 0.1
    waited=$((waited + 1))
  done
  [[ "$file" -nt "$marker" ]] || warn "padmap started but has published no controllers yet"
}

# Keep the daemon alive for as long as the game is.
#
# Modelled on killswitch_start, and for the same reason: this shell is about
# to be replaced by the emulator, so the keeper is a background process that
# polls the game's pid and exits on its own when the game is gone. What it
# watches for is the one thing that is its to fix -- no daemon at all, which
# takes the uinput clones with it and leaves the game holding nothing. A
# daemon running older code is *not* restarted: that is a sync's business,
# and swapping the daemon mid-level would drop the clones the game is using.
# ensure-daemon --check exits 1 for both, so the reason is read off what it
# prints rather than off its status.
padmap_keeper_start() {
  local pid="$1"
  [[ "${GOTG_PADMAP_KEEPER:-1}" != "0" ]] || return 0
  command -v "$(padmap_bin)" >/dev/null 2>&1 || return 0
  (
    interval="${GOTG_PADMAP_KEEPER_INTERVAL:-5}"
    while kill -0 "$pid" 2>/dev/null; do
      sleep "$interval"
      kill -0 "$pid" 2>/dev/null || break
      said="$("$(padmap_bin)" ensure-daemon --check 2>&1)" && continue
      [[ "$said" == *"no daemon running"* ]] || continue
      # Following the game, and not fresh: the seats it had are the ones
      # the level is being played with.
      if "$(padmap_bin)" ensure-daemon --follow "$pid" >/dev/null 2>&1; then
        warn "padmap had stopped; started it again"
      else
        warn "padmap stopped and would not start again"
      fi
    done
  ) &
}

# Where the launch-time controller check is, or nothing.
#
# On PATH first. Failing that, beside `gotg-ui`: they are built and installed
# as one package, so a machine with the picker has the check even when only the
# picker's directory made it onto PATH -- which is the ordinary case, because
# nothing puts a second entry there for a command nobody types.
padmap_seat_bin() {
  local named="${GOTG_SEAT:-}" beside dir
  if [[ -n "$named" ]]; then
    printf '%s' "$named"
    return 0
  fi
  if command -v gotg-seat >/dev/null 2>&1; then
    printf 'gotg-seat'
    return 0
  fi
  if beside="$(command -v gotg-ui 2>/dev/null)"; then
    beside="$(dirname "$(readlink -f "$beside")")/gotg-seat"
    if [[ -x "$beside" ]]; then
      printf '%s' "$beside"
      return 0
    fi
  fi
  # The Nix profiles, by name, because a launch from Steam has none of them on
  # PATH -- the same list the generated Steam launcher walks to find the picker
  # itself. This is what "the check was skipped" actually was: not a machine
  # without gotg-seat, but a PATH without the directory holding it, on the one
  # launch path where nobody sees the warning that says so.
  for dir in "$HOME/.nix-profile/bin" "$HOME/.local/state/nix/profile/bin" \
    /nix/var/nix/profiles/default/bin; do
    [[ -x "$dir/gotg-seat" ]] || continue
    printf '%s' "$dir/gotg-seat"
    return 0
  done
  return 1
}

# What Steam leaves in the environment of anything it starts, taken back out.
#
# Steam hands a game it launches SDL_GAMECONTROLLER_IGNORE_DEVICES naming the
# controllers Steam Input is handling on its behalf, so the game uses Steam's
# virtual gamepad instead of the physical one. These emulators do not speak
# Steam Input, and padmap's clones mirror the physical pad's identity -- so an
# ignore rule Steam wrote for the Steam Controller silenced its clone too, and
# Ryujinx sat on "Waiting for controller connection" for a pad that was there.
# Measured: inside the sandbox, SDL listed the clone until the variable was
# set, and not after.
#
# The per-game Steam launcher has undone this since the first Steam entry. The
# picker does not go through that launcher: it calls `gotg play` itself, so a
# game started from the picker, from Steam, inherited all of it through the
# picker. Here, then -- the one place every route passes -- and before the
# seat gate, which is SDL too.
#
# LD_PRELOAD is Steam's overlay, which nix-wrapped programs cannot load
# ("libGL.so.1: cannot open shared object file", the picker on a Deck). Only
# that entry is dropped; anything else in there is somebody's own doing.
padmap_clear_steam_env() {
  unset SDL_GAMECONTROLLER_IGNORE_DEVICES SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT
  export SDL_JOYSTICK_HIDAPI=0 SDL_JOYSTICK_DISABLE_UDEV=0
  if [[ "${LD_PRELOAD:-}" == *gameoverlayrenderer* ]]; then
    local kept=() entry
    for entry in ${LD_PRELOAD//:/ }; do
      [[ "$entry" == *gameoverlayrenderer* ]] || kept+=("$entry")
    done
    if ((${#kept[@]})); then
      LD_PRELOAD="$(IFS=:; printf '%s' "${kept[*]}")"
      export LD_PRELOAD
    else
      unset LD_PRELOAD
    fi
  fi
}

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
  if ! seat="$(padmap_seat_bin)" || ! command -v "$seat" >/dev/null 2>&1; then
    # Said out loud. A check that is quietly not there is indistinguishable
    # from a check that ran and was happy, and the difference is a game
    # starting with nothing to play it with.
    warn "no gotg-seat here; starting without checking for a controller"
    return 0
  fi
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
    padmap_no_sandbox_for "${PLAY_ATTR:-}" && export PADMAP_NO_ISOLATE=1
    exec "$(padmap_rs_bin)" exec -- "$@"
  fi
  exec "$@"
}

# Does this environment bring up a compositor of its own?
#
# padmap's sandbox is a user namespace, and inside one every file owned by
# root reads as `nobody` -- /tmp/.X11-unix among them. wlroots will not put an
# X socket in a directory "not owned by root or us", so a nested sway never
# starts Xwayland, the gamescope inside it is handed no output, and the
# screen stays black. It says nothing about sandboxes while doing it.
#
# So those sessions run outside it. They lose nothing: each copy inside gets
# its own seat, which isolates a game from the other players' pads more
# precisely than one sandbox around the lot ever did.
#
# The environment says so itself, with a marker file its derivation carries.
padmap_no_sandbox_for() {
  local attr="$1"
  [[ -n "$attr" ]] || return 1
  [[ -e "$(env_root "$attr")/share/gotg/owns-session" ]]
}

# --- what padmap wrote, into the emulator this launch is about ---------------
#
# The daemon writes Ryujinx's, Cemu's and Dolphin's bindings -- and, since
# motion arrived, where each finds padmap's DSU server -- into the *default*
# config directories under the home. Every emulator gotg runs is isolated
# under its own environment, so those files are never the ones it reads.
# `padmap-rs emit` exists for exactly this: the same writer, pointed at the
# directories the launch will use.

# padmap's own rule for where its daemon leaves things (runtime::dir_under).
padmap_runtime_dir() {
  printf '%s' "${GOTG_PADMAP_RUNTIME:-${XDG_RUNTIME_DIR:-/tmp}/padmap}"
}

# The pads padmap has published, in the shape `emit` takes on stdin.
#
# Read off env.sh rather than asked of the daemon: it is rewritten on every
# republish, it is what `padmap-rs exec` hands the game, and each mapping line
# in it already names the pad -- GUID first, then "padmap Player N". Nothing
# else in it is needed here: the capabilities the list can carry are for the
# ares writer, and gotg binds ares itself.
padmap_published() {
  local file value
  file="$(padmap_runtime_dir)/env.sh"
  [[ -f "$file" ]] || return 1
  # padmap's own file, sourced the way exec would source it, in a shell that
  # cannot touch this one.
  # shellcheck disable=SC1090
  value="$(set +u; . "$file" 2>/dev/null; printf '%s' "${SDL_GAMECONTROLLERCONFIG:-}")" || return 1
  [[ -n "$value" ]] || return 1
  jq -cR '
    select(length > 0)
    | (split(",")) as $f
    | ($f[1] // "" | capture("^padmap Player (?<n>[0-9]+)$")? // empty) as $m
    | {player: ($m.n | tonumber), guid: $f[0], name: $f[1], sdl_line: .}
  ' <<<"$value" | jq -cs 'select(length > 0)'
}

# Have padmap write this environment's emulator configuration. Returns 0 when
# something was written, so a caller that snapshots the result knows to.
#
# Every destination is named, including the three this environment is not:
# an unnamed one means the real location, and the point is that nothing lands
# in the home. ares is gotg's own writer, so padmap's goes to scratch.
padmap_emit() {
  local attr="$1" manifest emulator state pads scratch
  command -v "$(padmap_rs_bin)" >/dev/null 2>&1 || return 1
  manifest="$(env_pads_manifest "$attr")"
  [[ -f "$manifest" ]] || return 1
  emulator="$(jq -r '.emulator // ""' "$manifest")"
  case "$emulator" in ryujinx | cemu | dolphin) ;; *) return 1 ;; esac
  pads="$(padmap_published)" || return 1

  state="$(env_state_dir "$attr")"
  scratch="$state/padmap-scratch"
  mkdir -p "$scratch" || return 1
  local ryujinx="$scratch/Config.json" cemu="$scratch/cemu" dolphin="$scratch/dolphin-emu"
  case "$emulator" in
    ryujinx) ryujinx="$(pads_ryujinx_config "$attr")" ;;
    cemu) cemu="$(pads_cemu_config_dir "$attr")/controllerProfiles" ;;
    dolphin) dolphin="$state/config/dolphin-emu" ;;
  esac

  local written
  written="$("$(padmap_rs_bin)" emit \
    --ryujinx-config "$ryujinx" \
    --cemu-dir "$cemu" \
    --dolphin-dir "$dolphin" \
    --ares-settings "$scratch/settings.bml" \
    --env-file "$scratch/env.sh" <<<"$pads" 2>/dev/null)" || return 1
  rm -rf "$scratch"
  # Only the file this environment reads counts as written; the scratch ones
  # were the price of naming every destination.
  grep -qF "$state/" <<<"$written" || return 1
  log "padmap wrote the $emulator bindings and motion for $attr"
}
