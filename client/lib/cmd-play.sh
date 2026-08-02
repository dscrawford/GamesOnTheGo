# shellcheck shell=bash
# install / play / sync — the commands that touch nix and Steam.

cmd_install() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg install <id>"
  manifest_ensure

  local game attr launcher
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game")"

  download_game "$game"

  # Refresh rather than skip when a root is already there. This runs from a
  # terminal where nix is cheap and a cached build is quick, and the alternative
  # is what bit twice already: an environment whose definition has moved keeps
  # running the old one, silently, until somebody thinks to run sync.
  if env_is_built "$attr"; then
    env_refresh "$attr" ||
      warn "could not rebuild $attr — carrying on with the one already built here"
  else
    env_build "$attr"
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
  local game attr
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game")"

  # Before the download, not after. A missing emulator is the failure most likely
  # to need a person, and finding that out at the end of a 10 GB transfer helps
  # nobody. Once built it is a symlink test, so the usual launch pays nothing.
  env_ensure "$attr"

  download_game "$game"

  local target install env_state
  target="$(resolve_target "$game")"
  install="$(game_local_path "$game")"
  env_state="$(env_state_dir "$attr")"

  # Carry forward saves written before this environment was told where to keep
  # them. Once only, and never at the cost of the launch: the game running
  # matters more than the copy, and a Steam launch is where most of these will
  # be started from, so it goes to the log and play continues either way.
  saves_adopt_once "$attr" || warn "could not adopt older saves for $attr"

  # The environment decides the emulator, its arguments and its settings; all it
  # is told is which file to run, where that came from, and where to keep what
  # it writes — that last one from env_state_dir, so the CLI and the wrapper
  # agree on it rather than each computing their own.
  export GOTG_TARGET="$target" GOTG_INSTALL="$install" GOTG_ENV_STATE="$env_state"

  log "launching $(manifest_field "$game" title) with $attr"
  exec "$(env_bin "$attr")" "$target" "$@"
}

# Rebuild the GC roots after pulling a new version of the flake.
cmd_sync() {
  local flake
  flake="$(gotg_flake)"
  [[ -f "$flake/flake.nix" ]] || die "no flake at $flake (set GOTG_FLAKE or the 'flake' key in $GOTG_CONFIG_FILE)"

  mkdir -p "$GOTG_STATE_DIR"
  log "building gotg -> $GOTG_APP_ROOT"
  "$(nix_bin)" build "$flake#gotg" -o "$GOTG_APP_ROOT" || die "could not build gotg from $flake"

  # Only rebuild the environments that are already in use here.
  local root name before after changed=0
  if [[ -d "$GOTG_ROOTS_DIR" ]]; then
    for root in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$root" ]] || continue
      name="$(basename "$root")"
      # Roots from before environments existed were named after the emulator, and
      # the flake has no such attribute any more.
      if [[ "$name" != env-* ]]; then
        warn "skipping $name: not an environment. Remove it with: rm $root"
        continue
      fi
      before="$(readlink -f "$root" 2>/dev/null || true)"
      log "rebuilding $name"
      env_build "$name"
      after="$(readlink -f "$root" 2>/dev/null || true)"
      # Say which ones actually moved. A rebuild that changes nothing looks
      # identical to one that changes how a game runs, and the difference is
      # worth a line — a stale root is used silently for as long as it lasts.
      if [[ "$before" != "$after" ]]; then
        log "  $name changed"
        changed=$((changed + 1))
      fi
    done
  fi

  if ((changed > 0)); then
    log ""
    log "$changed environment(s) changed. Anything already open keeps the old one"
    log "until it is closed and launched again."
  fi
  log "done"
}
