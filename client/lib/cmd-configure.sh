# shellcheck shell=bash
# `gotg configure` — open an emulator's own settings screen against one
# environment's configuration.
#
# There has to be a command for this, because a launch cannot double as one.
# `gotg play` starts Dolphin with -b so the game comes up with no library
# window and the pad works; and every environment points the emulator at its own
# XDG directories, so simply running dolphin-emu from a terminal edits the
# player's personal install and changes nothing about the game they are trying
# to fix. This runs the environment's own emulator, against the environment's
# own settings, with no game and no batch flag.
#
# Only environments that isolate have anything to open, which is why the
# derivation says whether it can be configured rather than this guessing.

cmd_configure() {
  local want="${1:-}" variant=""
  [[ -n "$want" ]] || die "usage: gotg configure <id> [variant]"
  shift

  # Same shape as `gotg play <id> <variant>`: a bare word is the variant, and
  # anything starting with a dash is not.
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  manifest_cached || manifest_ensure
  local game attr
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game" "$variant")"

  local root="$GOTG_ROOTS_DIR/$attr"
  [[ -e "$root" ]] ||
    die "$attr is not built here yet — run: gotg install $want${variant:+ $variant}"

  local manifest="$root/share/gotg/configure.json"
  [[ -f "$manifest" ]] ||
    die "$attr has no settings screen to open.
     Emulators that keep their settings in-game are configured while playing."

  local emulator state
  emulator="$(jq -r '.exec' "$manifest")"
  [[ -x "$emulator" ]] || die "the emulator for $attr is missing: $emulator"

  state="$(env_state_dir "$attr")"
  # The emulator writes its settings on exit, so these have to exist first.
  mkdir -p "$state/config" "$state/data"

  log "$attr: opening $(basename "$emulator")"
  log ""
  log "This is that environment's own settings, not your Dolphin install's, and"
  log "not any other environment's. Changes apply on its next launch."
  log ""

  # Hand the process over rather than waiting on it: there is nothing to do
  # afterwards, and a settings screen is something you close when you are done.
  XDG_CONFIG_HOME="$state/config" XDG_DATA_HOME="$state/data" exec "$emulator"
}
