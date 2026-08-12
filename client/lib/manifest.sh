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

# A cached v1 manifest is not a catalog: its entries have no id, no handler
# and no member files, so it forces a refresh rather than yielding nulls.
manifest_cached() {
  [[ -s "$GOTG_CACHE_FILE" ]] &&
    jq -e '.version == 2' "$GOTG_CACHE_FILE" >/dev/null 2>&1
}

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
  service_have || {
    warn "no service configured — run: gotg login"
    return 1
  }
  local payload
  payload="$(service_curl -fsS --max-time "${GOTG_API_TIMEOUT:-120}" \
    "$(service_url)/catalog" 2>&1)" || {
    # The reason is in the captured output: a refused token file, a TLS error.
    [[ -n "$payload" ]] && warn "$payload"
    warn "could not fetch the catalog from $(service_url)/catalog."
    warn "Has the indexer run yet? It publishes the catalog after its first pass."
    return 1
  }
  jq -e '.version == 2 and (.games | type == "array")' >/dev/null 2>&1 <<<"$payload" || {
    warn "the catalog at $(service_url)/catalog is not valid GOTG JSON"
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

# Every game as {id, platform, handler, title, files: [{name, size_bytes,
# sha256}]}. The id is on the wire now; nothing derives it from a path.
manifest_games() {
  manifest_cached || die "no catalog cached — run: gotg refresh"
  jq -c '.games[]' "$GOTG_CACHE_FILE"
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
      # Check the record before anything builds a path out of it: every
      # member name becomes a local path component.
      validate_id "$(manifest_field "$matches" id)"
      validate_platform "$(manifest_field "$matches" platform)"
      local member
      while IFS= read -r member; do
        validate_filename "$member"
      done < <(jq -r '.files[].name' <<<"$matches")
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
# Single-file entries land under their canonical member name. Everything a
# recipe refines, and every tree fetched in place, lands as or under the id —
# `unzip` overrides included, which keep their old shape.
game_local_path() {
  local game="$1" platform name handler
  platform="$(manifest_field "$game" platform)"
  validate_platform "$platform"
  handler="$(manifest_field "$game" handler)"
  if [[ "$(override_field "$game" unzip)" == "true" ]]; then
    name="$(manifest_field "$game" id)"
  elif [[ "$handler" == "single_file" || "$handler" == "no_intro_set" ]]; then
    name="$(jq -r '.files[0].name' <<<"$game")"
    validate_filename "$name"
  else
    name="$(manifest_field "$game" id)"
  fi
  printf '%s/%s/%s' "$GOTG_GAMES_DIR" "$platform" "$name"
}

# A refined artifact keeps the id but the recipe picks the extension: resolve
# what is actually on disk — the base path, or the one id.<ext> beside it.
game_installed_path() {
  local base
  base="$(game_local_path "$1")"
  if [[ -e "$base" ]]; then
    printf '%s' "$base"
    return 0
  fi
  local matches=("$base".*)
  if [[ -e "${matches[0]}" ]]; then
    printf '%s' "${matches[0]}"
    return 0
  fi
  return 1
}

game_is_installed() { game_installed_path "$1" >/dev/null; }

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
      | [.platform, .id, ([.files[].size_bytes] | add), .files[0].name, .title] | @tsv
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
    "\($m)handler:  \($r) \(.handler)",
    "\($m)size:     \($r) \([.files[].size_bytes] | add) bytes",
    "\($m)files:    \($r) \(.files | length)",
    "\($m)local:    \($r) \($local)",
    "\($m)installed:\($r) \(if $installed == "yes" then $ok else $no end)\($installed)\($r)"
  ' <<<"$game"
}
