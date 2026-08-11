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

# Returns rather than dies on failure: the callers know whether a cached
# catalog makes the failure survivable, and a die here cannot be caught — it
# exits straight through `manifest_refresh || fallback`.
manifest_refresh() {
  config_load
  local token payload
  token="$(api_login)" || {
    warn "could not reach $GOTG_SERVER to refresh the catalog"
    return 1
  }
  payload="$(api_fetch "$(manifest_remote_path)" "$token")" || {
    warn "could not fetch the catalog from $GOTG_SERVER$(manifest_remote_path)."
    warn "Has the importer run yet? It publishes the catalog after its first import."
    return 1
  }
  jq -e '.games | type == "array"' >/dev/null 2>&1 <<<"$payload" || {
    warn "catalog at $(manifest_remote_path) is not valid GOTG JSON"
    return 1
  }

  mkdir -p "$GOTG_STATE_DIR"
  local tmp="$GOTG_CACHE_FILE.tmp"
  printf '%s' "$payload" >"$tmp"
  mv "$tmp" "$GOTG_CACHE_FILE"

  local count
  count="$(jq '.games | length' "$GOTG_CACHE_FILE")"
  log "catalog updated: $count game(s)"
}

# Refresh only when there is nothing usable cached, or it has aged out. A
# failed refresh over a stale cache is survivable; over no cache it is not.
manifest_ensure() {
  if ! manifest_cached; then
    manifest_refresh || die "no catalog cached and none could be fetched"
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

cmd_refresh() { manifest_refresh || die "catalog refresh failed"; }

# How many games a bare `gotg list` prints. A full library runs to hundreds,
# and a screenful you can read beats a scrollback you have to hunt through.
GOTG_LIST_LIMIT=50

cmd_list() {
  local pattern="" limit="$GOTG_LIST_LIMIT"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all)
        limit=0
        shift
        ;;
      --limit)
        limit="${2:-}"
        shift 2 || die "--limit needs a number"
        ;;
      --limit=*)
        limit="${1#--limit=}"
        shift
        ;;
      -*) die "unknown option for list: $1" ;;
      *)
        [[ -z "$pattern" ]] || die "usage: gotg list [pattern] [--all|--limit N]"
        pattern="$1"
        shift
        ;;
    esac
  done
  [[ "$limit" =~ ^[0-9]+$ ]] || die "--limit takes a number, not: $limit"

  manifest_ensure
  local rows
  # The entry name comes along so the installed check is an exact path test
  # rather than a guess at what the file is called.
  #
  # The pattern is a regex, matched without case against the id, the title and
  # the platform — the three things anyone would type. `any` rather than three
  # `or`s, because a bare `(.id, .title)` emits one result per field and would
  # print a matching game once per field it matched in.
  if ! rows="$(manifest_games |
    jq -r --arg p "$pattern" '
      select($p == "" or ([.id, .title, .platform] | any(test($p; "i"))))
      | [.platform, .id, .size_bytes, (.path | split("/") | last), .title] | @tsv
    ' 2>&1)"; then
    die "not a valid pattern: $pattern
     It is a regex, so a bare . matches any character and ( must be closed."
  fi

  if [[ -z "$rows" ]]; then
    if [[ -n "$pattern" ]]; then
      log "nothing matches $pattern"
      return 0
    fi
    log "the catalog is empty"
    return 0
  fi

  local total shown
  total="$(wc -l <<<"$rows")"
  shown="$total"
  if ((limit > 0 && total > limit)); then
    rows="$(head -n "$limit" <<<"$rows")"
    shown="$limit"
  fi

  # The colour goes in its own argument so the width applies to the value and
  # not to the escape bytes, which would silently break every column.
  local platform id size name title status c_status
  printf '%s%-3s %-9s %-46s %10s  %s%s\n' \
    "$C_HEAD" "" "PLATFORM" "ID" "SIZE" "TITLE" "$C_RESET"
  while IFS=$'\t' read -r platform id size name title; do
    if [[ -e "$GOTG_GAMES_DIR/$platform/$name" ]]; then
      status="[*]"
      c_status="$C_OK"
    else
      status="[ ]"
      c_status="$C_MUTED"
    fi
    printf '%s%-3s%s %s%-9s%s %s%-46s%s %10s  %s\n' \
      "$c_status" "$status" "$C_RESET" \
      "$C_MUTED" "$platform" "$C_RESET" \
      "$C_ID" "$id" "$C_RESET" \
      "$(human_size "$size")" "$title"
  done <<<"$rows"
  log ""
  # Braced: "$C_OK[*]" reads as an array subscript.
  log "${C_OK}[*]${C_RESET} installed locally"

  # Say what was left out, and how to see it. A silent truncation reads as
  # "that is everything", which is the one thing it must not read as.
  if ((shown < total)); then
    log ""
    log "${C_WARN}showing $shown of $total.${C_RESET} Narrow it with a pattern:"
    log "  gotg list zelda          # id, title or platform, as a regex"
    log "  gotg list --all          # every one of them"
  fi
}

cmd_info() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg info <id>"
  manifest_ensure
  local game
  game="$(manifest_find "$want")"

  local installed="no"
  game_is_installed "$game" && installed="yes"

  # Labels muted so the values are what the eye lands on; the two fields worth
  # answering at a glance — what it is, and whether it is here — get colour.
  jq -r --arg local "$(game_local_path "$game")" --arg installed "$installed" \
    --arg m "$C_MUTED" --arg r "$C_RESET" --arg id "$C_ID" \
    --arg ok "$C_OK" --arg no "$C_MUTED" '
    "\($m)id:       \($r) \($id)\(.id)\($r)",
    "\($m)title:    \($r) \(.title)",
    "\($m)platform: \($r) \(.platform)",
    "\($m)type:     \($r) \(.type)",
    "\($m)size:     \($r) \(.size_bytes) bytes",
    "\($m)sha256:   \($r) \(.sha256 // "-")",
    "\($m)remote:   \($r) \(.path)",
    "\($m)local:    \($r) \($local)",
    "\($m)installed:\($r) \(if $installed == "yes" then $ok else $no end)\($installed)\($r)"
  ' <<<"$game"
}
