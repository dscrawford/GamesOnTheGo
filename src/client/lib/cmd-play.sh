# shellcheck shell=bash
# install / play / sync — the commands that touch nix and Steam.

cmd_install() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg install <id> [variant]"
  local variant="${2:-}"
  manifest_ensure

  local library game lattr root launcher
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: a game is installed from one now.
     Make one:  nix flake init -t github:dscrawford/GamesOnTheGo/feat/nix-games#library
     then:      gotg library <that directory>"
  game="$(manifest_find "$want")"
  lattr="$(library_attr "$game" "$variant")"
  root="$(library_games_dir)/$lattr"

  if [[ "${GOTG_PROGRESS_LINES:-}" == "1" ]] && ! game_is_installed "$game"; then
    # For the picker, the game builds while its files download: two waits of
    # minutes each, one after the other, were the whole of a first launch.
    # The spec alone is built first -- an evaluation, cheap -- so a broken
    # definition still stops it before a transfer that can run to tens of
    # gigabytes, and the download knows the environment whose recipe it runs.
    mkdir -p "$(library_games_dir)"
    "$(nix_bin)" build "$library#$lattr.gotgSpecFile" -o "$root.spec" ||
      die "could not evaluate $lattr from $library; nothing was downloaded"
    launch_spec_load "$root.spec"
    library_build "$library" "$lattr" &
    # shellcheck disable=SC2034 # read by env_build_wait, in env.sh
    GOTG_ENV_BUILD_PID=$!
    download_game "$game"
    env_build_wait
    rm -f "$root.spec"
  else
    library_build "$library" "$lattr" || die "could not build $lattr from $library"
    # After the game's build: a raw source that needs processing is processed
    # by the recipe its environment carries.
    launch_spec_load "$root/share/gotg/spec.json"
    download_game "$game"
  fi

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

  # A game is its library's Nix output (library-play.sh, docs/nix-games.md).
  local library
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: a game is played from one now.
     Make one:  nix flake init -t github:dscrawford/GamesOnTheGo/feat/nix-games#library
     then:      gotg library <that directory>"
  library_play "$library" "$want" "$variant" "$want_version" "$@"
}

# Everything from choosing the game to exec'ing it, shared by `play` and
# `launch` (cmd-launch.sh): the same download, firmware, keys, saves, danstick,
# bindings, session and overlay, whichever of them chose the game.
play_launch() {
  local want="$1" variant="$2" want_version="$3"
  shift 3

  play_prepare "$want" "$variant" "$want_version"

  log "launching $(manifest_field "$PLAY_GAME" title) with $PLAY_ATTR"

  # Steam's controller and overlay settings, off, on every route -- see
  # danstick_clear_steam_env for the launch that found the picker's route
  # missing it.
  danstick_clear_steam_env

  # And the identity this environment wants its clones to have, before the
  # daemon is asked after: a decompiled port reads its own controller
  # database, and what the clone claims to be decides whether it is in it.
  danstick_identity_apply "$PLAY_ATTR"

  # Nobody is asked about controllers here: pairing is the overlay's, over the
  # game. danstick only has to be running, and to have published its pads.
  danstick_launch_ready

  # And the bindings again, now that danstick has published. The ones
  # play_prepare wrote were from what SDL saw before the daemon was up --
  # the raw pads, which `danstick-rs exec` is about to hide from the game -- so
  # ares' port 1 named a controller the emulator could not see, and the one
  # it could see was named by nothing. Seen on a real launch: the Steam
  # Controller seated as player one, published, and dead in the game.
  pads_configure "$PLAY_ATTR" || warn "could not set controller bindings for $PLAY_ATTR"

  # Before the watcher, which reads it from the environment it inherits.
  play_session "$$"

  # "$$" survives the exec below, so what the watcher holds is the emulator.
  killswitch_start "$$" "$(killswitch_console "$PLAY_ATTR" "$(manifest_field "$PLAY_GAME" platform)")" "$PLAY_ATTR"
  danstick_keeper_start "$$"
  danstick_exec "$(env_bin "$PLAY_ATTR")" "$PLAY_TARGET" "$@"
}

# A directory this one play shares with the overlay over it and the
# environment's wrapper under it: where a save picked from the overlay is
# named, and the game's pid for the overlay to stop it by. See the end of
# gotg-play in env/lib.nix for what happens there.
#
# Private to this user, since what is written in it is run: under
# XDG_RUNTIME_DIR, and with mktemp's 0700 wherever it is. Named by the play's
# pid so a later play can clear the ones whose play is gone.
play_session() {
  local pid="$1" base dir name old
  if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "$XDG_RUNTIME_DIR" ]]; then
    base="$XDG_RUNTIME_DIR/gotg"
    [[ -d "$base" ]] || mkdir -m 700 "$base"
    for old in "$base"/session-*; do
      [[ -d "$old" ]] || continue
      name="${old##*/session-}"
      [[ "${name%%.*}" =~ ^[0-9]+$ ]] || continue
      kill -0 "${name%%.*}" 2>/dev/null || rm -rf -- "$old"
    done
    dir="$(mktemp -d "$base/session-$pid.XXXXXX")" || return 0
  else
    dir="$(mktemp -d "${TMPDIR:-/tmp}/gotg-session-$pid.XXXXXX")" || return 0
  fi
  export GOTG_SESSION_DIR="$dir"
  export GOTG_SESSION_CLIENT="$GOTG_ROOT/bin/gotg"
}

# Everything a launch needs short of running the emulator, shared by play and
# qa. Leaves PLAY_GAME, PLAY_ATTR and PLAY_TARGET set, and the GOTG_* launch
# variables exported.
play_prepare() {
  local want="$1" variant="${2:-}" want_version="${3:-}"

  # Prefer the cached catalog: a game already installed here must still launch
  # when the server is unreachable.
  # A `gotg launch` spec carries the game and its environment's name: no
  # catalog to consult, and no resolving -- Nix resolved it.
  [[ -n "${GOTG_PINNED_GAME:-}" ]] || manifest_cached || manifest_ensure
  PLAY_GAME="$(manifest_find "$want")"
  if [[ -n "${GOTG_PINNED_ATTR:-}" ]]; then
    PLAY_ATTR="$GOTG_PINNED_ATTR"
  else
    PLAY_ATTR="$(env_attr "$PLAY_GAME" "$variant")"
  fi
  local game="$PLAY_GAME" attr="$PLAY_ATTR"

  # Before the download, not after. A missing emulator is the failure most likely
  # to need a person, and finding that out at the end of a 10 GB transfer helps
  # nobody. Once built it is a symlink test, so the usual launch pays nothing.
  # The environment is the spec's, built by Nix as a dependency of the game.
  env_is_built "$attr" || die "no gotg-play in $(env_root "$attr"), the environment this launch names"
  # The GL this machine loads if it has none of its own: see foreign-gl.sh.
  foreign_gl_ensure "$attr"

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

# `sync` was every environment's GC root, rebuilt from the flake; a game is its
# library's output now, and `update` rebuilds those -- and the client and the
# picker Steam starts -- so `sync` is the name it had.
cmd_sync() {
  [[ "${1:-}" != "--force" && "${1:-}" != "-f" ]] || shift
  log "${C_DIM}sync is gotg update now${C_RESET}"
  cmd_update "$@"
}

