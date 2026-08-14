# shellcheck shell=bash
# install / play / sync — the commands that touch nix and Steam.

cmd_install() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg install <id>"
  manifest_ensure

  local game attr launcher
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game")"

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

  # After the environment: a raw source that needs processing is processed by
  # the recipe that environment carries.
  download_game "$game"

  launcher="$(launcher_write "$game")"
  log ""
  log "installed: $(manifest_field "$game" title)"
  log "launcher:  $launcher"
  log ""
  log "Add it to Steam with Games -> Add a Non-Steam Game -> Browse, and pick that file."
}

cmd_play() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg play <id> [variant] [emulator args...]"
  shift || true

  # An optional second word names a variant — a mod or a different engine for
  # this one game. Anything starting with a dash is an emulator argument, so the
  # two cannot be confused; anything else is taken as a variant and must exist,
  # rather than being passed silently to the emulator where a typo would look
  # like the mod simply not working.
  local variant=""
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  # Prefer the cached catalog: a game already installed here must still launch
  # when the server is unreachable.
  manifest_cached || manifest_ensure
  local game attr
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game" "$variant")"

  # Before the download, not after. A missing emulator is the failure most likely
  # to need a person, and finding that out at the end of a 10 GB transfer helps
  # nobody. Once built it is a symlink test, so the usual launch pays nothing.
  env_ensure "$attr"

  download_game "$game"

  local target install env_state
  target="$(resolve_target "$game")"
  install="$(game_installed_path "$game")" ||
    die "nothing on disk for $(manifest_field "$game" id) after download"
  env_state="$(env_state_dir "$attr")"

  # Carry forward saves written before this environment was told where to keep
  # them. Once only, and never at the cost of the launch: the game running
  # matters more than the copy, and a Steam launch is where most of these will
  # be started from, so it goes to the log and play continues either way.
  saves_adopt_once "$attr" || warn "could not adopt older saves for $attr"

  # Take the latest save from the remote before the emulator opens it. Only
  # when nothing can be lost — the local set must be unchanged since the last
  # sync — and never at the cost of the launch. In a subshell so its scratch
  # directory and trap belong to a process that will actually exit: the exec
  # below replaces this one, and an EXIT trap does not survive that.
  (
    saves_tmp_init
    saves_pull_auto "$attr"
  ) || warn "could not take the latest save for $attr — launching with what is here"

  # Point the emulator at whatever controller is actually plugged in, every
  # launch, so a new pad needs no visit to a settings screen. Never fatal: a
  # launch with no controller is a launch on the keyboard, which beats not
  # starting.
  pads_configure "$attr" || warn "could not set controller bindings for $attr"

  # Console keys, for the platforms that cannot decrypt a game without them.
  # Fetched once and kept with the environment; a launch without them still
  # starts, because the emulator's own complaint about a specific game is more
  # use than ours about a file.
  local platform
  platform="$(manifest_field "$game" platform)"
  keys_ensure "$attr" "$platform" ||
    warn "could not fetch console keys for $attr"

  # Firmware, for the platform whose emulator stops on a dialog without it.
  # Cached once per platform and hardlinked in, so only the first environment
  # ever pays for the download.
  firmware_ensure "$attr" "$platform" ||
    warn "could not prepare firmware for $attr"

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
  # A URL ref answers from nix's fetch cache for up to an hour; sync exists
  # to pick up what just changed, so it pays for a fresh look at the head.
  local -a refresh=()
  if flake_is_path "$flake"; then
    [[ -f "$flake/flake.nix" ]] || die "no flake at $flake (set GOTG_FLAKE or the 'flake' key in $GOTG_CONFIG_FILE)"
  else
    refresh=(--refresh)
  fi

  mkdir -p "$GOTG_STATE_DIR"
  log "building gotg -> $GOTG_APP_ROOT"
  "$(nix_bin)" build "$flake#gotg" -o "$GOTG_APP_ROOT" "${refresh[@]}" || die "could not build gotg from $flake"

  # Only rebuild the environments that are already in use here.
  local root name before after changed=0
  if [[ -d "$GOTG_ROOTS_DIR" ]]; then
    for root in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$root" ]] || continue
      name="$(basename "$root")"
      # Anything that is not a well-formed environment name: a root from before
      # environments existed, named after the emulator, or one left by a
      # mistyped `nix build -o`. Checked against the same pattern env_attr
      # produces, and skipped rather than fatal — one stray symlink in here
      # should not stop every other environment from being rebuilt.
      if ! [[ "$name" =~ $GOTG_ATTR_RE ]]; then
        warn "skipping $name: not an environment name. Remove it with: rm $root"
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
