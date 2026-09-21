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
  # `gotg steam add` does those six manual steps, artwork and all; printing
  # them here only taught the long way round.
  log "Put it in Steam with: gotg steam add $(manifest_field "$game" id)"
}

cmd_play() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg play <id> [variant] [emulator args...]"
  shift || true

  # An optional second word names a variant — a mod or a different engine for
  # this one game. Anything starting with a dash is an emulator argument, so the
  # two cannot be confused; anything else is taken as a variant and must exist,
  # rather than being passed silently to the emulator where a typo would look
  # like the mod simply not working. (--emulate is the one exception, handled
  # in the loop below, because it names a variant rather than an argument.)
  local variant=""
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  # --version picks which update of the game to run. Pulled out of the
  # arguments rather than passed through: everything after them belongs to the
  # emulator, and a Switch update is not an emulator's business.
  local want_version=""
  local -a rest=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version)
        want_version="${2:-}"
        [[ -n "$want_version" ]] || die "usage: gotg play <id> [variant] --version <version>"
        shift 2
        ;;
      --version=*)
        want_version="${1#--version=}"
        shift
        ;;
      # The same thing as the `emulate` variant, spelled as a flag. Both
      # exist because both get reached for: the word reads better in a list
      # of variants, the flag reads better after an id somebody already
      # typed. --force-emu is here because it is what it was asked for by.
      --emulate | --force-emu)
        variant=emulate
        shift
        ;;
      *)
        rest+=("$1")
        shift
        ;;
    esac
  done
  set -- "${rest[@]+"${rest[@]}"}"

  play_prepare "$want" "$variant" "$want_version"

  log "launching $(manifest_field "$PLAY_GAME" title) with $PLAY_ATTR"

  # Steam's controller and overlay settings, off, on every route -- see
  # padmap_clear_steam_env for the launch that found the picker's route
  # missing it. Before the gate, which is SDL and would be blinded the same.
  padmap_clear_steam_env

  # And the identity this environment wants its clones to have, before the
  # daemon is asked after: a decompiled port reads its own controller
  # database, and what the clone claims to be decides whether it is in it.
  padmap_identity_apply "$PLAY_ATTR"

  # Before the kill switch rather than after: the gate draws a window, and the
  # watcher is holding the pid that is about to become the game.
  padmap_seat_gate \
    "$(manifest_field "$PLAY_GAME" platform)" \
    "$(manifest_field "$PLAY_GAME" title)"

  # And the bindings again, now that the gate has seated somebody. The ones
  # play_prepare wrote were from what SDL saw before anyone held a button --
  # the raw pads, which `padmap-rs exec` is about to hide from the game -- so
  # ares' port 1 named a controller the emulator could not see, and the one
  # it could see was named by nothing. Seen on a real launch: the Steam
  # Controller seated as player one, published, and dead in the game.
  pads_configure "$PLAY_ATTR" || warn "could not set controller bindings for $PLAY_ATTR"

  # "$$" survives the exec below, so what the watcher holds is the emulator.
  killswitch_start "$$"
  padmap_keeper_start "$$"
  padmap_exec "$(env_bin "$PLAY_ATTR")" "$PLAY_TARGET" "$@"
}

# Everything a launch needs short of running the emulator, shared by play and
# qa. Leaves PLAY_GAME, PLAY_ATTR and PLAY_TARGET set, and the GOTG_* launch
# variables exported.
play_prepare() {
  local want="$1" variant="${2:-}" want_version="${3:-}"

  # Prefer the cached catalog: a game already installed here must still launch
  # when the server is unreachable.
  manifest_cached || manifest_ensure
  PLAY_GAME="$(manifest_find "$want")"
  PLAY_ATTR="$(env_attr "$PLAY_GAME" "$variant")"
  local game="$PLAY_GAME" attr="$PLAY_ATTR"

  # Before the download, not after. A missing emulator is the failure most likely
  # to need a person, and finding that out at the end of a 10 GB transfer helps
  # nobody. Once built it is a symlink test, so the usual launch pays nothing.
  env_ensure "$attr"

  download_game "$game"

  local install env_state
  PLAY_TARGET="$(resolve_target "$game")"
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
  # Which update to run, if this game has any: what was asked for, what the
  # environment's mod can take, or the newest. The environment does the
  # registering; all it is told is the answer.
  local version
  version="$(versions_resolve "$game" "$attr" "$want_version")"
  [[ -z "$version" ]] || log "version: $version"

  export GOTG_TARGET="$PLAY_TARGET" GOTG_INSTALL="$install" GOTG_ENV_STATE="$env_state"
  export GOTG_GAME_VERSION="$version"
}

# Rebuild the GC roots after pulling a new version of the flake.
cmd_sync() {
  local flake force=""
  # No dialog. Sync narrates itself, a line per environment, and a progress
  # window on top of that is the same news twice — and under the installer,
  # which runs a sync after every upgrade, it is a window nobody asked for.
  export GOTG_NO_DIALOG=1
  [[ "${1:-}" != "--force" && "${1:-}" != "-f" ]] || force=1
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
  # The flake as it was the last time everything here was built from it. The
  # same commit again is the same closure — nix would say so too, after an
  # evaluation per environment that this is here to skip.
  local stamp="$GOTG_STATE_DIR/sync.rev" fingerprint
  fingerprint="$(flake_fingerprint "$flake")"
  if [[ -z "$force" && -n "$fingerprint" && -f "$stamp" && "$(cat "$stamp")" == "$fingerprint" ]]; then
    _sync_mark same gotg "at ${fingerprint:0:12}"
    local root
    for root in "$GOTG_ROOTS_DIR"/env-*; do
      [[ -e "$root" ]] && _sync_mark same "$(basename "$root")"
    done
    log "${C_DIM}nothing changed since ${fingerprint:0:12}; gotg sync --force builds anyway${C_RESET}"
    return 0
  fi
  rm -f "$stamp"

  local before after
  before="$(readlink -f "$GOTG_APP_ROOT" 2>/dev/null || true)"
  "$(nix_bin)" build "$flake#gotg" -o "$GOTG_APP_ROOT" "${refresh[@]}" || die "could not build gotg from $flake"
  after="$(readlink -f "$GOTG_APP_ROOT" 2>/dev/null || true)"
  _sync_mark "$([[ "$before" != "$after" ]] && echo changed || echo same)" gotg

  # The picker too, into a root of its own. The Steam entry runs whichever
  # gotg-ui it finds, and finding it on PATH meant finding whichever build
  # was on PATH when Steam started -- on a machine that develops this, three
  # builds behind by the evening. The launcher looks here first. Best effort:
  # a machine without the picker's dependencies still syncs its games.
  before="$(readlink -f "$GOTG_UI_ROOT" 2>/dev/null || true)"
  if "$(nix_bin)" build "$flake#gotg-ui" -o "$GOTG_UI_ROOT" "${refresh[@]}" 2>"$GOTG_STATE_DIR/sync-gotg-ui.log"; then
    after="$(readlink -f "$GOTG_UI_ROOT" 2>/dev/null || true)"
    _sync_mark "$([[ "$before" != "$after" ]] && echo changed || echo same)" gotg-ui
  else
    _sync_mark failed gotg-ui "$GOTG_STATE_DIR/sync-gotg-ui.log"
  fi

  # Only rebuild the environments that are already in use here. One line
  # each, and a failure marks its line rather than ending the pass — a
  # platform whose build broke should not keep the others stale.
  #
  # Several at a time: the builds are independent, most of a sync is waiting
  # on nix, and a machine with a dozen environments spent that wait one
  # environment at a time. The cap is what keeps a Deck from trying to
  # compile twelve emulators at once; nix does its own scheduling under it.
  local root name changed=0 failed=0
  local -a wanted=()
  if [[ -d "$GOTG_ROOTS_DIR" ]]; then
    for root in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$root" ]] || continue
      name="$(basename "$root")"
      # Anything that is not a well-formed environment name: a root from before
      # environments existed, named after the emulator, or one left by a
      # mistyped `nix build -o`. Checked against the same pattern env_attr
      # produces, and skipped rather than fatal — one stray symlink in here
      # should not stop every other environment from being rebuilt.
      # The build-key file that sits beside every root. Skipped quietly
      # rather than reported: sync writes these itself, and telling a person
      # to `rm` a file the tool just made is noise every single run.
      [[ "$name" == *.by ]] && continue
      if ! [[ "$name" =~ $GOTG_ATTR_RE ]]; then
        _sync_mark skipped "$name" "not an environment name; rm $root"
        continue
      fi
      wanted+=("$name")
      # Taken before anything starts: what the root pointed at when this sync
      # began is what "changed" is measured against.
      printf '%s\n' "$(readlink -f "$root" 2>/dev/null || true)" \
        >"$GOTG_STATE_DIR/sync-$name.before"
    done

    local jobs="${GOTG_SYNC_JOBS:-4}"
    [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || jobs=4
    for name in "${wanted[@]+"${wanted[@]}"}"; do
      # One slot at a time, so the cap is a cap rather than a suggestion.
      while (($(jobs -rp | wc -l) >= jobs)); do wait -n; done
      (
        if (GOTG_BUILD_QUIET=1 env_build "$name") 2>"$GOTG_STATE_DIR/sync-$name.log"; then
          printf 'built\n' >"$GOTG_STATE_DIR/sync-$name.result"
        else
          printf 'failed\n' >"$GOTG_STATE_DIR/sync-$name.result"
        fi
      ) &
    done
    wait

    # Reported in the order they were found, never the order they finished:
    # which build was quickest is not something to read a list by.
    for name in "${wanted[@]+"${wanted[@]}"}"; do
      before="$(cat "$GOTG_STATE_DIR/sync-$name.before" 2>/dev/null || true)"
      rm -f "$GOTG_STATE_DIR/sync-$name.before"
      if [[ "$(cat "$GOTG_STATE_DIR/sync-$name.result" 2>/dev/null || true)" != built ]]; then
        _sync_mark failed "$name" "$GOTG_STATE_DIR/sync-$name.log"
        failed=$((failed + 1))
        continue
      fi
      after="$(readlink -f "$GOTG_ROOTS_DIR/$name" 2>/dev/null || true)"
      if [[ "$before" != "$after" ]]; then
        _sync_mark changed "$name"
        changed=$((changed + 1))
      else
        _sync_mark same "$name"
      fi
    done
  fi

  ((changed == 0)) || log "${C_DIM}anything already open keeps its old environment until relaunched${C_RESET}"
  ((failed == 0)) || die "$failed environment(s) did not build"
  # Only a complete pass earns the stamp: a partial one must be paid for again.
  [[ -z "$fingerprint" ]] || printf '%s\n' "$fingerprint" >"$stamp"
}

# ● name — green for a root that moved, dim for one that did not, yellow for
# a skip, red for a build that failed. The note is where to look next.
_sync_mark() {
  local state="$1" name="$2" note="${3:-}" dot
  case "$state" in
    changed) dot="${C_OK}●${C_RESET}" ;;
    same) dot="${C_DIM}●${C_RESET}" ;;
    skipped) dot="${C_WARN}●${C_RESET}" ;;
    *) dot="${C_ERROR}●${C_RESET}" ;;
  esac
  printf '%s %-24s %s%s%s\n' "$dot" "$name" "$C_DIM" "${note:-$state}" "$C_RESET" >&2
}
