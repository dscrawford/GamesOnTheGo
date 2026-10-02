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
# A root is kept for the couch: offline, a game already built still runs.

gotg_library() {
  local library="${GOTG_LIBRARY:-}"
  [[ -n "$library" ]] || library="$(config_get library 2>/dev/null || true)"
  printf '%s' "$library"
}

library_games_dir() { printf '%s/games' "$GOTG_STATE_DIR"; }

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
  "$(nix_bin)" build "$library#$attr" -o "$root" 2>"$err" || rc=$?
  if ((rc != 0)) && grep -q "does not provide attribute" "$err"; then
    log "$attr is newer than the library's catalog; updating it"
    "$(nix_bin)" flake update catalog --flake "$library" >&2 || true
    rc=0
    "$(nix_bin)" build "$library#$attr" -o "$root" 2>"$err" || rc=$?
  fi
  ((rc == 0)) || cat "$err" >&2
  rm -f "$err"
  return "$rc"
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
  if ! library_build "$library" "$attr"; then
    exe="$(library_exe "$root")" ||
      die "could not build $attr from $library, and it has never been built here"
    warn "could not rebuild $attr from $library -- running the build already here"
  fi
  exe="$(library_exe "$root")" || die "$root has no program to run"
  exec "$exe" ${want_version:+--version "$want_version"} "$@"
}

# Every game with a root, rebuilt from the library: sync, per game.
cmd_update() {
  local library root attr failed=0
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  [[ -d "$(library_games_dir)" ]] || {
    log "no games built here yet"
    return 0
  }
  for root in "$(library_games_dir)"/*; do
    [[ -e "$root" ]] || continue
    attr="$(basename "$root")"
    [[ "$attr" =~ ^[a-z0-9][a-z0-9_-]*\.[a-z]{3,5}\.[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)?$ ]] || continue
    if library_build "$library" "$attr"; then
      log "$attr: up to date"
    else
      warn "$attr: could not rebuild; the build already here still runs"
      failed=$((failed + 1))
    fi
  done
  ((failed == 0)) || die "$failed game(s) did not rebuild"
}
