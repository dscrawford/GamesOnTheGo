# shellcheck shell=bash
# The game catalog.
#
# The importer publishes it at <remote_root>/.gotg/manifest.json; this keeps a
# local copy so `gotg list` works offline and Steam launches do not depend on the
# server being reachable when the game is already installed.
#
# Ids are unique per platform but not globally — the same title exists on both the
# N64 and SNES sets, so "usa.bugs_life" is two different games. A bare id is
# accepted whenever it is unambiguous, and "platform/id" always works.

MANIFEST_MAX_AGE="${GOTG_MANIFEST_MAX_AGE:-86400}" # refresh a day-old catalog

manifest_remote_path() { printf '%s/.gotg/manifest.json' "${GOTG_REMOTE_ROOT%/}"; }

manifest_cached() { [[ -s "$GOTG_CACHE_FILE" ]]; }

manifest_is_stale() {
  manifest_cached || return 0
  local age now mtime
  now="$(date +%s)"
  mtime="$(stat -c '%Y' "$GOTG_CACHE_FILE")"
  age=$((now - mtime))
  ((age > MANIFEST_MAX_AGE))
}

manifest_refresh() {
  config_load
  local token payload
  token="$(api_login)"
  payload="$(api_fetch "$(manifest_remote_path)" "$token")" || {
    die "could not fetch the catalog from $GOTG_SERVER$(manifest_remote_path).
     Has the importer run yet? It publishes the catalog after its first import."
  }
  jq -e '.games | type == "array"' >/dev/null 2>&1 <<<"$payload" ||
    die "catalog at $(manifest_remote_path) is not valid GOTG JSON"

  mkdir -p "$GOTG_STATE_DIR"
  local tmp="$GOTG_CACHE_FILE.tmp"
  printf '%s' "$payload" >"$tmp"
  mv "$tmp" "$GOTG_CACHE_FILE"

  local count
  count="$(jq '.games | length' "$GOTG_CACHE_FILE")"
  log "catalog updated: $count game(s)"
}

# Refresh only when there is nothing usable cached, or it has aged out.
manifest_ensure() {
  if ! manifest_cached; then
    manifest_refresh
  elif manifest_is_stale; then
    manifest_refresh || warn "using the cached catalog"
  fi
}

# Every game as {id, platform, path, type, size_bytes, sha256, title}.
manifest_games() {
  manifest_cached || die "no catalog cached — run: gotg refresh"
  jq -c '
    .games[]
    | . + {id: (
        (.path | split("/") | last) as $name
        | if .type == "file" and ($name | test("\\."))
          then ($name | sub("\\.[^.]+$"; ""))
          else $name end
      )}
  ' "$GOTG_CACHE_FILE"
}

# Resolve a user-supplied id to exactly one game, or explain why it cannot.
manifest_find() {
  local want="$1"
  local platform="" matches
  local id="$want"
  if [[ "$want" == */* ]]; then
    platform="${want%%/*}"
    id="${want#*/}"
  fi
  validate_id "$id"

  matches="$(manifest_games | jq -c --arg id "$id" --arg pf "$platform" \
    'select(.id == $id and ($pf == "" or .platform == $pf))')"

  local count
  count="$(printf '%s' "$matches" | grep -c . || true)"
  case "$count" in
    0) die "no game called '$want' in the catalog. Try: gotg list | grep $id" ;;
    1)
      # Check the record before anything builds a path out of it.
      validate_id "$(manifest_field "$matches" id)"
      validate_platform "$(manifest_field "$matches" platform)"
      validate_remote_path "$(manifest_field "$matches" path)"
      printf '%s' "$matches"
      ;;
    *)
      local options
      options="$(printf '%s\n' "$matches" | jq -r '"  gotg <command> \(.platform)/\(.id)"')"
      die "'$id' is ambiguous — it exists on several platforms:
$options"
      ;;
  esac
}

manifest_field() {
  jq -r --arg f "$2" '.[$f] // empty' <<<"$1"
}

# Local install path for a game: ~/Games/<platform>/<entry name>.
#
# A game marked `unzip` lands as a directory named after its id instead: the
# No-Intro sets ship zipped ROMs, which most emulators read directly, but native
# ports want the bare ROM file.
game_local_path() {
  local game="$1" platform name
  platform="$(manifest_field "$game" platform)"
  validate_platform "$platform"
  if [[ "$(override_field "$game" unzip)" == "true" ]]; then
    name="$(manifest_field "$game" id)"
  else
    name="$(basename "$(manifest_field "$game" path)")"
  fi
  printf '%s/%s/%s' "$GOTG_GAMES_DIR" "$platform" "$name"
}

game_is_installed() {
  local path
  path="$(game_local_path "$1")"
  [[ -e "$path" ]]
}

cmd_refresh() { manifest_refresh; }

cmd_list() {
  manifest_ensure
  local rows
  # The entry name comes along so the installed check is an exact path test
  # rather than a guess at what the file is called.
  rows="$(manifest_games |
    jq -r '[.platform, .id, .size_bytes, (.path | split("/") | last), .title] | @tsv')"
  [[ -n "$rows" ]] || {
    log "the catalog is empty"
    return 0
  }

  local platform id size name title status
  printf '%-3s %-9s %-46s %10s  %s\n' "" "PLATFORM" "ID" "SIZE" "TITLE"
  while IFS=$'\t' read -r platform id size name title; do
    if [[ -e "$GOTG_GAMES_DIR/$platform/$name" ]]; then status="[*]"; else status="[ ]"; fi
    printf '%-3s %-9s %-46s %10s  %s\n' \
      "$status" "$platform" "$id" "$(human_size "$size")" "$title"
  done <<<"$rows"
  log ""
  log "[*] installed locally"
}

cmd_info() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg info <id>"
  manifest_ensure
  local game
  game="$(manifest_find "$want")"

  local installed="no"
  game_is_installed "$game" && installed="yes"

  jq -r --arg local "$(game_local_path "$game")" --arg installed "$installed" '
    "id:        \(.id)",
    "title:     \(.title)",
    "platform:  \(.platform)",
    "type:      \(.type)",
    "size:      \(.size_bytes) bytes",
    "sha256:    \(.sha256 // "-")",
    "remote:    \(.path)",
    "local:     \($local)",
    "installed: \($installed)"
  ' <<<"$game"
}
