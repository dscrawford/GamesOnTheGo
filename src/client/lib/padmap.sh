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
