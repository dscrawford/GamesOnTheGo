# shellcheck shell=bash
# `gotg play` through a library flake (docs/nix-games.md).
#
# With a library configured -- the `library` config key, or GOTG_LIBRARY -- a
# game is its Nix output: `<library>#<platform>.<region>.<name>[.<variant>]`,
# built into a GC root of its own under games/ and run. The picker and Steam
# both run `gotg play`, so they play games this way without knowing it. That
# output runs `gotg launch --spec`, which is play's own path from there.
#
# The catalog is still the client's cache -- what the picker searches, and
# what turns an id into its platform here. The library pins its own copy, so a
# game newer than the pin updates the library's `catalog` input, once, first.
# A root is kept for the couch: a game already built launches from its root
# with no evaluation and no network, and is rebuilt only when the library has
# moved since -- its flake.lock, stamped beside the root (library_stamp).
# `gotg update` rebuilds them all.

# The environment, then the config, then the library this client was run
# from (`nix run <library>#ui` sets GOTG_LIBRARY_DEFAULT to its own store
# copy): a configured one wins over that, because a pin can only be moved in
# a writable copy.
gotg_library() {
  local library="${GOTG_LIBRARY:-}"
  [[ -n "$library" ]] || library="$(config_get library 2>/dev/null || true)"
  [[ -n "$library" ]] || library="${GOTG_LIBRARY_DEFAULT:-}"
  printf '%s' "$library"
}

library_games_dir() { printf '%s/games' "$GOTG_STATE_DIR"; }

# What a root's name under games/ may be: platform.region.name, a variant
# after it. The one gate between a directory listing or a cache file and the
# Nix expression updates.sh evaluates, so it is named once.
GOTG_ROOT_ATTR_RE='^[a-z0-9][a-z0-9_-]*\.[a-z]{3,5}\.[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)?$'

# What a library is, as of now: its flake.lock's hash for a directory, the
# reference itself for anything else (a URL is re-resolved by `update`).
library_stamp() {
  local library="$1"
  if [[ -f "$library/flake.lock" ]]; then
    printf '%s %s' "$library" "$(sha256sum "$library/flake.lock" | cut -d' ' -f1)"
  else
    printf '%s' "$library"
  fi
}

# Built here, from the library as it is now: launch it as it is.
library_current() {
  local library="$1" attr="$2" root
  root="$(library_games_dir)/$attr"
  library_exe "$root" >/dev/null || return 1
  [[ "$(cat "$root.by" 2>/dev/null)" == "$(library_stamp "$library")" ]]
}

# The attribute a game is in a library: platform.region.name, and a variant
# after it. An id is region.name already.
library_attr() {
  local game="$1" variant="${2:-}" platform id
  platform="$(manifest_field "$game" platform)"
  id="$(manifest_field "$game" id)"
  validate_platform "$platform"
  validate_id "$id"
  [[ -z "$variant" || "$variant" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "invalid variant name: $variant"
  printf '%s.%s%s' "$platform" "$id" "${variant:+.$variant}"
}

# Build one game into its root. A game the library's pinned catalog does not
# have yet -- added on the website since the lock -- updates the pin once and
# is tried again. So does a pin the server has moved past: the lock names the
# catalog by hash, and once that file has left the store Nix fetches the url
# again, gets today's, and refuses it as a narHash mismatch.
library_build() {
  local library="$1" attr="$2" root err rc=0
  root="$(library_games_dir)/$attr"
  mkdir -p "$(library_games_dir)"
  err="$(mktemp)"
  (library_build_once "$library" "$attr" "$root") 2>"$err" || rc=$?
  if ((rc != 0)) && grep -q "does not provide attribute\|mismatch in field 'narHash'" "$err"; then
    log "$attr is newer than the library's catalog, or the catalog has moved on; updating it"
    "$(nix_bin)" flake update catalog --flake "$library" >&2 || true
    rc=0
    (library_build_once "$library" "$attr" "$root") 2>"$err" || rc=$?
  fi
  # Still missing after the catalog moved: the library no longer has it at
  # all -- a variant replaced, as PaperBoat replaced Paper Mario's recut.
  # Gone is not broken, and says so with its own status.
  if ((rc != 0)) && grep -q "does not provide attribute" "$err"; then
    rm -f "$err"
    return 3
  fi
  ((rc != 0)) || library_stamp "$library" >"$root.by"
  # What nix and the dialog said, whatever came of it: a dialog that could
  # not start is worth knowing about on a build that worked.
  cat "$err" >&2
  # Cancelled by the person watching: that is an answer, not a build to fall
  # back from.
  if grep -q "build stopped: the progress dialog was cancelled" "$err"; then
    rm -f "$err"
    exit 1
  fi
  rm -f "$err"
  return "$rc"
}

# One build, drawn where nobody can see a terminal: under Steam a first build
# that shows no window for twenty minutes reads as a crash, so a dialog pulses
# for as long as it runs (env.sh's, shared with the environment builds).
library_build_once() {
  local library="$1" attr="$2" root="$3"
  if [[ -z "${GOTG_NO_DIALOG:-}" ]] && ! is_tty && has_display && have_zenity; then
    _env_build_zenity "$library#$attr" "$root" "$attr"
  else
    # For the picker, nix's build log as the stage lines its loading screen
    # shows (env.sh).
    _nix_drawn build "$library#$attr" -o "$root"
  fi
}

# The program a game's root runs: its one binary.
library_exe() {
  local root="$1" exe
  for exe in "$root"/bin/*; do
    [[ -x "$exe" ]] && {
      printf '%s' "$exe"
      return 0
    }
  done
  return 1
}

library_play() {
  local library="$1" want="$2" variant="$3" want_version="$4"
  shift 4
  manifest_cached || manifest_ensure
  local game attr root exe
  game="$(manifest_find "$want")"
  attr="$(library_attr "$game" "$variant")"
  root="$(library_games_dir)/$attr"
  if library_current "$library" "$attr"; then
    :
  elif ! library_build "$library" "$attr"; then
    exe="$(library_exe "$root")" ||
      die "could not build $attr from $library, and it has never been built here"
    warn "could not rebuild $attr from $library -- running the build already here"
  fi
  exe="$(library_exe "$root")" || die "$root has no program to run"
  exec "$exe" ${want_version:+--version "$want_version"} "$@"
}

# The library's pinned catalog, as the cache: `nix flake update catalog`
# moves the pin to what the server has now (a library that is a store path,
# or not ours to write, keeps the pin it has), and the cache becomes a GC
# root to the file the lock names. Two catalogs -- the service's and the
# lock's -- were two answers to "what is there", and a launch builds from
# the lock's. Replacing the old cache file first: nix's -o over a regular
# file is an error, not a root.
library_catalog_refresh() {
  local library="$1"
  mkdir -p "$GOTG_STATE_DIR"
  if ! "$(nix_bin)" flake update catalog --flake "$library" 2>"$GOTG_STATE_DIR/catalog-update.log"; then
    warn "could not update the library's catalog; showing the one it has ($GOTG_STATE_DIR/catalog-update.log)"
  fi
  [[ -L "$GOTG_CACHE_FILE" ]] || rm -f "$GOTG_CACHE_FILE"
  "$(nix_bin)" build "$library#catalog" -o "$GOTG_CACHE_FILE" || {
    warn "could not read the catalog from $library"
    return 1
  }
  manifest_cached || {
    warn "the catalog $library pins is not valid GOTG JSON"
    return 1
  }
  log "catalog updated: $(jq '.games | length' "$GOTG_CACHE_FILE") game(s)"
}

# `gotg library [ref]`: which library games are played from, or set it.
cmd_library() {
  local ref="${1:-}"
  if [[ -z "$ref" ]]; then
    local library
    library="$(gotg_library)"
    [[ -n "$library" ]] || die "no library configured; gotg library <flake ref or directory>"
    printf '%s\n' "$library"
    return 0
  fi
  # A directory is taken as an absolute path: config is read from anywhere.
  if [[ -d "$ref" ]]; then
    ref="$(cd "$ref" && pwd)"
    [[ -f "$ref/flake.nix" ]] || die "$ref has no flake.nix; nix flake init -t <gotg>#library makes one"
  fi
  config_patch "$(jq -nc --arg l "$ref" '{library: $l}')"
  log "games are played from $ref"
  # And `gotg` in Nix's registry for this user, so it is `nix run gotg#ui`
  # and `nix search gotg zelda` from anywhere -- a path repeated on every
  # line was the whole of the interface's length. Best effort: a registry
  # Nix will not write leaves the long form, which still works.
  if "$(nix_bin)" registry add gotg "$ref" 2>/dev/null; then
    log "and \`gotg\` names it: nix run gotg#ui, nix search gotg zelda"
  else
    warn "could not add gotg to the flake registry; nix run $ref#ui still works"
  fi
}

# GOTG itself brought up to date, from the picker's chip: the library's pin
# moved to the head of where it came from (and its catalog with it), the
# client and the picker built from there with nothing pointed at them yet,
# and only then the two roots Steam starts swapped to them, each an atomic
# symlink and a GC root of its own (`nix build <store path> -o`). Anything
# that fails -- or a cancel from the loader, which is a TERM to the group --
# undoes all of it: the roots relinked to what they pointed at (the symlink
# is what Nix's gcroot names, so relinking it keeps the root) and the lock
# back from its copy, so the machine is exactly as it was. Games are not
# rebuilt here; the picker that restarts shows an exclamation mark where
# their roots moved, and Play or Update does each one. The last line names
# the picker now at its root, for the running picker to restart into.
#
# The check that follows runs as the *new* client, so a fix to the check
# ships with the update.
library_update_self() {
  local library
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  [[ -d "$library" && -w "$library" && "$library" != /nix/store/* ]] ||
    die "the library at $library is not a directory this can write; its pin cannot be moved from here"
  [[ -f "$library/flake.lock" ]] || die "the library at $library has no flake.lock to move"
  with_lock "$GOTG_STATE_DIR/locks/library.lock" "" "" _library_update_self_locked "$library"
}

# library_update_self's work, one at a time per machine.
_library_update_self_locked() {
  local library="$1" app_out ui_out app_was ui_was
  export GOTG_NO_DIALOG=1
  app_was="$(readlink "$GOTG_APP_ROOT" 2>/dev/null || true)"
  ui_was="$(readlink "$GOTG_UI_ROOT" 2>/dev/null || true)"
  cp -f "$library/flake.lock" "$library/flake.lock.before-update"
  _update_self_relink() {
    if [[ -n "$2" ]]; then
      ln -sfn "$2" "$1"
    elif [[ -L "$1" ]]; then
      rm -f "$1"
    fi
  }
  _update_self_undo() {
    trap - TERM INT HUP
    _update_self_relink "$GOTG_APP_ROOT" "$app_was"
    _update_self_relink "$GOTG_UI_ROOT" "$ui_was"
    [[ -f "$library/flake.lock.before-update" ]] || return 0
    mv -f "$library/flake.lock.before-update" "$library/flake.lock"
    warn "the library's lock is back as it was; nothing was changed"
  }
  _update_self_fail() {
    _update_self_undo
    die "$1"
  }
  trap '_update_self_undo; exit 143' TERM INT HUP
  log "moving the library to the newest gotg"
  "$(nix_bin)" flake update gotg catalog --flake "$library" >&2 || _update_self_fail "could not move the library's pin"
  # Drawn for the picker's loading screen, then asked for the paths, which
  # is instant the second time.
  _nix_drawn build "$library#gotg" "$library#gotg-ui" --no-link || _update_self_fail "could not build gotg and the picker from $library"
  app_out="$("$(nix_bin)" build "$library#gotg" --no-link --print-out-paths)" || _update_self_fail "could not build gotg from $library"
  ui_out="$("$(nix_bin)" build "$library#gotg-ui" --no-link --print-out-paths)" || _update_self_fail "could not build the picker from $library"
  "$(nix_bin)" build "$app_out" -o "$GOTG_APP_ROOT" || _update_self_fail "could not place gotg at $GOTG_APP_ROOT"
  "$(nix_bin)" build "$ui_out" -o "$GOTG_UI_ROOT" || _update_self_fail "could not place the picker at $GOTG_UI_ROOT"
  trap - TERM INT HUP
  rm -f "$library/flake.lock.before-update"
  log "gotg: up to date"
  log "gotg-ui: up to date"
  if [[ -x "$GOTG_APP_ROOT/bin/gotg" ]]; then
    "$GOTG_APP_ROOT/bin/gotg" update --check --force >/dev/null 2>&1 || true
  fi
  foreign_gl_sync
  printf 'picker\t%s\n' "$(readlink -f "$GOTG_UI_ROOT")"
}

# One game brought up to date: every root it has here -- the plain game and
# its variants, each an output of its own -- rebuilt from the library as it
# is, and then whatever its install lacks fetched (an update, DLC, a pack:
# download.sh's top-up). What Play would do on its way to the game, done
# now without playing; what the picker's Update row runs.
library_update_game() {
  local key="$1" library attr rc found=0 failed=0 game
  [[ "$key" =~ ^[a-z0-9][a-z0-9_-]*/[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$ ]] || die "usage: gotg update <platform>/<id>"
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  while IFS= read -r attr; do
    [[ -n "$attr" && "$(updates_attr_key "$attr")" == "$key" ]] || continue
    found=$((found + 1))
    rc=0
    library_build "$library" "$attr" || rc=$?
    if ((rc == 0)); then
      log "$attr: up to date"
    elif ((rc == 3)); then
      warn "$attr is no longer in the library; its last build still runs"
    else
      warn "$attr: could not rebuild; the build already here still runs"
      failed=$((failed + 1))
    fi
  done < <(library_root_attrs)
  ((found > 0)) || log "$key has no build here yet; a play will make one"
  if manifest_cached && game="$(manifest_find "$key" 2>/dev/null)"; then
    _top_up_extras "$game"
  fi
  ((failed == 0)) || die "$failed build(s) of $key did not finish"
}

# The attributes with a root under games/: platform.region.name, a variant
# after it. A root's stamp and an install's spec sit beside them and are not
# games; nothing else is expected there, and anything else is left alone.
library_root_attrs() {
  local root attr
  for root in "$(library_games_dir)"/*; do
    [[ -e "$root" ]] || continue
    attr="$(basename "$root")"
    [[ "$attr" != *.by && "$attr" != *.spec ]] || continue
    [[ "$attr" =~ $GOTG_ROOT_ATTR_RE ]] || continue
    printf '%s\n' "$attr"
  done
}

# Every game with a root rebuilt from the library -- and the client and picker
# Steam starts, which it re-exports (mkLibrary) -- then the GL a machine
# without its own needs, for what they now name.
#
cmd_update() {
  local library root attr failed=0
  case "${1:-}" in
    --check)
      shift
      updates_check "$@"
      updates_json
      return 0
      ;;
    */*)
      library_update_game "$1"
      return 0
      ;;
    self)
      library_update_self
      return 0
      ;;
    "") ;;
    *) die "usage: gotg update [--check [--force] | self | <platform>/<id>]" ;;
  esac
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  # It narrates itself, one line per game; a dialog on top of that is the
  # same news twice, and under the installer a window nobody asked for.
  export GOTG_NO_DIALOG=1
  "$(nix_bin)" build "$library#gotg" -o "$GOTG_APP_ROOT" || die "could not build gotg from $library"
  log "gotg: up to date"
  # Best effort: a machine without the picker's dependencies still updates.
  if "$(nix_bin)" build "$library#gotg-ui" -o "$GOTG_UI_ROOT" 2>"$GOTG_STATE_DIR/update-gotg-ui.log"; then
    log "gotg-ui: up to date"
  else
    warn "gotg-ui: could not build; see $GOTG_STATE_DIR/update-gotg-ui.log"
  fi
  mkdir -p "$(library_games_dir)"
  while IFS= read -r attr; do
    [[ -n "$attr" ]] || continue
    root="$(library_games_dir)/$attr"
    local rc=0
    library_build "$library" "$attr" || rc=$?
    if ((rc == 0)); then
      log "$attr: up to date"
    elif ((rc == 3)); then
      # Not removed for the person: the last build still runs, saves and
      # all, and may be the one they wanted.
      warn "$attr is no longer in the library; its last build still runs. To let it go: rm $root $root.by"
    else
      warn "$attr: could not rebuild; the build already here still runs"
      failed=$((failed + 1))
    fi
  done < <(library_root_attrs)
  foreign_gl_sync
  ((failed == 0)) || die "$failed game(s) did not rebuild"
}
