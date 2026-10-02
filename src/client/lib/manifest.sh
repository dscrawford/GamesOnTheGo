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
  # Behind a library the pin is the truth until `refresh` moves it, or a
  # game it lacks does (library_build): a cache aging out would have every
  # launch move the pin on its own.
  [[ -z "$(gotg_library)" || -n "${GOTG_LIBRARY_MOUNT:-}" ]] || return 1
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
  # With a library, the catalog is what its lock pins: the thing a launch
  # builds from, and so the one thing to show (library_catalog_refresh).
  # With the library mounted (QA in the cluster) the service's copy carries
  # the paths a download copies from; that still comes from the service.
  if [[ -n "$(gotg_library)" && -z "${GOTG_LIBRARY_MOUNT:-}" ]]; then
    library_catalog_refresh "$(gotg_library)"
    return
  fi
  service_have || {
    warn "no service configured — run: gotg login"
    return 1
  }
  # With the library mounted here, the catalog is asked for its server paths
  # too: what the download copies from disk instead of streaming.
  local payload query=""
  [[ -z "${GOTG_LIBRARY_MOUNT:-}" ]] || query="?paths=1"
  payload="$(service_curl -fsS --max-time "${GOTG_API_TIMEOUT:-120}" \
    --max-filesize "${GOTG_CATALOG_MAX_BYTES:-104857600}" \
    "$(service_url)/catalog$query" 2>&1)" || {
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

# Where the bytes live. The catalog names its own byte host (files_url) so a
# deployment can keep /games off the proxied control plane; an older service
# or cache names none, and the one service url then serves both.
manifest_files_url() { manifest_files_hosts | head -n1; }

# Every byte host, one per line, in the catalog's order of preference: the
# tailnet address first for a machine on it, the universal host behind. A
# caller that sits beside the service — a QA pod on the cluster — names its
# own with GOTG_FILES_URL and hears of no other.
manifest_files_hosts() {
  # A launch spec's, in its order: the catalog it came from named them.
  if [[ -n "${GOTG_PINNED_FILES_URLS:-}" ]]; then
    printf '%s\n' "$GOTG_PINNED_FILES_URLS" | grep -E '^https?://' || true
    return 0
  fi
  if [[ "${GOTG_FILES_URL:-}" == http://* || "${GOTG_FILES_URL:-}" == https://* ]]; then
    printf '%s\n' "${GOTG_FILES_URL%/}"
    return 0
  fi
  local hosts=""
  manifest_cached && hosts="$(jq -r '
    [(.files_urls // [])[], (.files_url // empty)]
    | map(select(type == "string" and (startswith("http://") or startswith("https://"))))
    | reduce .[] as $h ([]; if index($h) then . else . + [$h] end) | .[]' "$GOTG_CACHE_FILE" 2>/dev/null)"
  if [[ -n "$hosts" ]]; then
    printf '%s\n' "$hosts"
  else
    printf '%s\n' "$(service_url)"
  fi
}

# The first byte host that answers, probed with a short connect timeout: a
# tailnet address is a black hole from outside it, and one probe per download
# beats one timeout per member. None answering falls back to the first, whose
# failure then says which host could not be reached.
manifest_files_pick() {
  local host first=""
  while IFS= read -r host; do
    [[ -n "$host" ]] || continue
    : "${first:=$host}"
    if service_curl -fsS -o /dev/null --connect-timeout 3 --max-time 10 "$host/healthz" 2>/dev/null; then
      printf '%s' "$host"
      return 0
    fi
  done < <(manifest_files_hosts)
  printf '%s' "$first"
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

  # The game a `gotg launch` spec carries, with no catalog to look in.
  if [[ -n "${GOTG_PINNED_GAME:-}" ]] &&
    [[ "$(manifest_field "$GOTG_PINNED_GAME" platform)/$(manifest_field "$GOTG_PINNED_GAME" id)" == "$platform/$id" ]]; then
    matches="$GOTG_PINNED_GAME"
    validate_id "$(manifest_field "$matches" id)"
    validate_platform "$(manifest_field "$matches" platform)"
    local member
    while IFS= read -r member; do
      validate_filename "$member"
    done < <(jq -r '.files[].name' <<<"$matches")
    printf '%s' "$matches"
    return 0
  fi

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

# Whether the entry carries updates or DLC beside the game (members under
# extras/). Such a game always installs through its recipe, as a directory.
game_has_extras() {
  jq -e '[.files[]?.name | startswith("extras/")] | any' <<<"$1" >/dev/null 2>&1
}

# The member that is the game itself. Not files[0]: the service lists members
# by name, and "extras/" sorts before most things.
game_base_member() {
  jq -r '[.files[]? | select(.name | startswith("extras/") | not)][0].name // empty' <<<"$1"
}

# The single-file handlers install their one member as it is named — unless
# an unzip override or attached extras hand the game to a recipe instead.
game_is_placed_file() {
  local game="$1" handler
  handler="$(manifest_field "$game" handler)"
  [[ "$handler" == "single_file" || "$handler" == "no_intro_set" ]] &&
    [[ "$(override_field "$game" unzip)" != "true" ]] &&
    ! game_has_extras "$game"
}

# Local install path for a game: ~/Games/<platform>/<entry name>.
#
# Single-file entries land under their canonical member name. Everything a
# recipe refines, and every tree fetched in place, lands as or under the id —
# `unzip` overrides included, which keep their old shape.
game_local_path() { game_path_in "$1" "$GOTG_GAMES_DIR"; }

# The same, under one particular games directory.
game_path_in() {
  local game="$1" root="$2" platform name
  platform="$(manifest_field "$game" platform)"
  validate_platform "$platform"
  if [[ "$(override_field "$game" unzip)" == "true" ]]; then
    name="$(manifest_field "$game" id)"
  elif game_is_placed_file "$game"; then
    name="$(game_base_member "$game")"
    validate_filename "$name"
  else
    name="$(manifest_field "$game" id)"
  fi
  printf '%s/%s/%s' "$root" "$platform" "$name"
}

# A refined artifact keeps the id but the recipe picks the extension: resolve
# what is actually on disk — the base path, or the one id.<ext> beside it. The
# glob is only for the recipe handlers whose base *is* the bare id; a
# single-file game's base already carries its extension, and globbing there
# would mistake a leftover .sav sidecar for the game itself.
#
# Every games directory is searched, in order: a game is wherever it was
# downloaded to, and the default directory is only where the next one goes.
game_installed_path() {
  local game="$1" root
  while IFS= read -r root; do
    [[ -n "$root" ]] || continue
    _game_installed_in "$game" "$root" && return 0
  done < <(storage_dirs)
  return 1
}

_game_installed_in() {
  local game="$1" root="$2" base
  base="$(game_path_in "$game" "$root")"
  if [[ -e "$base" ]]; then
    printf '%s' "$base"
    return 0
  fi
  if ! game_is_placed_file "$game" && [[ "$(override_field "$game" unzip)" != "true" ]]; then
    local matches=("$base".*)
    if [[ -e "${matches[0]}" ]]; then
      printf '%s' "${matches[0]}"
      return 0
    fi
  fi
  # A single-file game installed before its updates arrived: still the game,
  # at the name it was placed under, and playable without them.
  local legacy
  if legacy="$(game_legacy_path "$game" "$root")" && [[ -f "$legacy" ]]; then
    printf '%s' "$legacy"
    return 0
  fi
  return 1
}

# Where a single-file entry that has since gained extras was installed, under
# one games directory.
game_legacy_path() {
  local game="$1" root="${2:-$GOTG_GAMES_DIR}" handler name
  handler="$(manifest_field "$game" handler)"
  [[ "$handler" == "single_file" || "$handler" == "no_intro_set" ]] || return 1
  game_has_extras "$game" || return 1
  name="$(game_base_member "$game")"
  validate_filename "$name"
  printf '%s/%s/%s' "$root" "$(manifest_field "$game" platform)" "$name"
}

game_is_installed() { game_installed_path "$1" >/dev/null; }

cmd_refresh() { manifest_refresh || die "catalog refresh failed"; }

# Every installed game as platform/id, one per line, by game_installed_path's
# rule: the member name, the bare id (a recipe's directory), or id.<ext> for
# the handlers whose recipe picks the extension.
#
# One find and one jq, never a probe per row. Measured on this library's
# 8,893 rows: a bash loop stat-ing each one took 1.25s, and 6.5s at five
# times the catalog — past the 5s the grid gives the question. This is 65ms,
# and the disk listing is the only thing that ever becomes a path: catalog
# strings are compared against it, never resolved, which is also why a member
# name with a slash in it can never be "installed" — nothing installs nested.
# Only contract-shaped platform and id ever leave, as complete_ids does.
manifest_installed_keys() {
  [[ -s "$GOTG_CACHE_FILE" ]] || return 0
  local disk roots=()
  mapfile -t roots < <(storage_dirs)
  disk="$(find "${roots[@]}" -mindepth 2 -maxdepth 2 -printf '%P\n' 2>/dev/null)" || disk=""
  jq -r --arg disk "$disk" --arg plat_re "$GOTG_PLATFORM_RE" --arg id_re "$GOTG_ID_RE" '
    ($disk | split("\n") | map(select(length > 0))) as $entries
    | (INDEX($entries[]; .)) as $exact
    | (reduce ($entries[] | split("/") | select(length == 2)) as $e
        ({}; .[$e[0]] += [$e[1]])) as $byplat
    | if .version != 2 then empty else
        .games[]
        | .platform as $plat | .id as $id
        | (([.files[]? | select((.name | type) == "string" and (.name | startswith("extras/") | not))][0].name) // "") as $name
        | (.handler // "") as $handler
        | select(($plat | type) == "string" and ($id | type) == "string")
        | select(($plat | test($plat_re)) and ($id | test($id_re)))
        | select(
            $exact[$plat + "/" + $name] != null
            or $exact[$plat + "/" + $id] != null
            or (($handler != "single_file" and $handler != "no_intro_set")
                and (($byplat[$plat] // []) | any(startswith($id + "."))))
          )
        | "\($plat)/\($id)"
      end' "$GOTG_CACHE_FILE" 2>/dev/null || true
}
