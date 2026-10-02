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
# is tried again.
library_build() {
  local library="$1" attr="$2" root err rc=0
  root="$(library_games_dir)/$attr"
  mkdir -p "$(library_games_dir)"
  err="$(mktemp)"
  (library_build_once "$library" "$attr" "$root") 2>"$err" || rc=$?
  if ((rc != 0)) && grep -q "does not provide attribute" "$err"; then
    log "$attr is newer than the library's catalog; updating it"
    "$(nix_bin)" flake update catalog --flake "$library" >&2 || true
    rc=0
    (library_build_once "$library" "$attr" "$root") 2>"$err" || rc=$?
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
}

# Every game with a root rebuilt from the library -- and the client and picker
# Steam starts, which it re-exports (mkLibrary) -- then the GL a machine
# without its own needs, for what they now name.
cmd_update() {
  local library root attr failed=0
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  "$(nix_bin)" build "$library#gotg" -o "$GOTG_APP_ROOT" || die "could not build gotg from $library"
  log "gotg: up to date"
  # Best effort: a machine without the picker's dependencies still updates.
  if "$(nix_bin)" build "$library#gotg-ui" -o "$GOTG_UI_ROOT" 2>"$GOTG_STATE_DIR/update-gotg-ui.log"; then
    log "gotg-ui: up to date"
  else
    warn "gotg-ui: could not build; see $GOTG_STATE_DIR/update-gotg-ui.log"
  fi
  mkdir -p "$(library_games_dir)"
  for root in "$(library_games_dir)"/*; do
    [[ -e "$root" ]] || continue
    attr="$(basename "$root")"
    # A root's stamp and an install's spec sit beside it; they are not games.
    [[ "$attr" != *.by && "$attr" != *.spec ]] || continue
    [[ "$attr" =~ ^[a-z0-9][a-z0-9_-]*\.[a-z]{3,5}\.[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)?$ ]] || continue
    if library_build "$library" "$attr"; then
      log "$attr: up to date"
    else
      warn "$attr: could not rebuild; the build already here still runs"
      failed=$((failed + 1))
    fi
  done
  foreign_gl_sync
  ((failed == 0)) || die "$failed game(s) did not rebuild"
}
