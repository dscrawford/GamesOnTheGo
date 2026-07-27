# shellcheck shell=bash
# install / play / sync — the commands that touch nix and Steam.

cmd_install() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg install <id>"
  manifest_ensure

  local game emulator launcher
  game="$(manifest_find "$want")"
  emulator="$(emulator_for_game "$game")"

  download_game "$game"

  if emulator_is_built "$emulator"; then
    log "emulator $emulator already built"
  else
    emulator_build "$emulator"
  fi

  launcher="$(launcher_write "$game")"
  log ""
  log "installed: $(manifest_field "$game" title)"
  log "launcher:  $launcher"
  log ""
  log "Add it to Steam with Games -> Add a Non-Steam Game -> Browse, and pick that file."
}

cmd_play() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg play <id>"
  shift || true

  # Prefer the cached catalog: a game already installed here must still launch
  # when the server is unreachable.
  manifest_cached || manifest_ensure
  local game emulator bin
  game="$(manifest_find "$want")"

  download_game "$game"

  emulator="$(emulator_for_game "$game")"
  bin="$(emulator_bin "$emulator")"
  if [[ ! -x "$bin" ]]; then
    die "emulator '$emulator' is not built yet.
     Steam's environment cannot evaluate nix, so this has to happen in a terminal:
       gotg install $want"
  fi

  local args=()
  mapfile -t args < <(emulator_args "$game" "$emulator")

  log "launching $(manifest_field "$game" title) with $emulator"
  exec "$bin" "${args[@]}" "$@"
}

# Rebuild the GC roots after pulling a new version of the flake.
cmd_sync() {
  local flake
  flake="$(gotg_flake)"
  [[ -f "$flake/flake.nix" ]] || die "no flake at $flake (set GOTG_FLAKE or the 'flake' key in $GOTG_CONFIG_FILE)"

  mkdir -p "$GOTG_STATE_DIR"
  log "building gotg -> $GOTG_APP_ROOT"
  nix build "$flake#gotg" -o "$GOTG_APP_ROOT" || die "could not build gotg from $flake"

  # Only rebuild emulators that are already in use here.
  local name
  if [[ -d "$GOTG_ROOTS_DIR" ]]; then
    for name in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$name" ]] || continue
      name="$(basename "$name")"
      log "rebuilding $name"
      emulator_build "$name"
    done
  fi
  log "done"
}
