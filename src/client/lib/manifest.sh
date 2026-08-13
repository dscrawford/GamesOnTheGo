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
    --max-filesize "${GOTG_CATALOG_MAX_BYTES:-104857600}" \
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

# Where the bytes live. The catalog names its own byte host (files_url) so a
# deployment can keep /games off the proxied control plane; an older service
# or cache names none, and the one service url then serves both.
manifest_files_url() {
  local base=""
  manifest_cached && base="$(jq -r '.files_url // empty' "$GOTG_CACHE_FILE" 2>/dev/null)"
  [[ "$base" == http://* || "$base" == https://* ]] || base=""
  printf '%s' "${base:-$(service_url)}"
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
# what is actually on disk — the base path, or the one id.<ext> beside it. The
# glob is only for the recipe handlers whose base *is* the bare id; a
# single-file game's base already carries its extension, and globbing there
# would mistake a leftover .sav sidecar for the game itself.
game_installed_path() {
  local game="$1" base handler
  base="$(game_local_path "$game")"
  if [[ -e "$base" ]]; then
    printf '%s' "$base"
    return 0
  fi
  handler="$(manifest_field "$game" handler)"
  if [[ "$handler" != "single_file" && "$handler" != "no_intro_set" &&
    "$(override_field "$game" unzip)" != "true" ]]; then
    local matches=("$base".*)
    if [[ -e "${matches[0]}" ]]; then
      printf '%s' "${matches[0]}"
      return 0
    fi
  fi
  return 1
}

game_is_installed() { game_installed_path "$1" >/dev/null; }

cmd_refresh() { manifest_refresh || die "catalog refresh failed"; }

# How many games a bare `gotg list` prints. A full library runs to hundreds,
# and a screenful you can read beats a scrollback you have to hunt through.
GOTG_LIST_LIMIT=50

cmd_list() {
  local pattern="" limit="$GOTG_LIST_LIMIT" platform="" page=""
  local usage="usage: gotg list [pattern] [page] [--search <re>] [--platform <p>] [--page N] [--all|--limit N]"

  # --search and --page are the named spellings of the two positionals, so a
  # setter is shared: name the same slot twice, whichever way, and it is a
  # mistake worth catching rather than a silent last-wins.
  local seen_pattern="" seen_page=""
  set_pattern() {
    [[ -z "$seen_pattern" ]] || die "$usage"
    seen_pattern=1
    pattern="$1"
  }
  set_page() {
    [[ -z "$seen_page" ]] || die "$usage"
    [[ "$1" =~ ^[1-9][0-9]{0,8}$ ]] || die "a page starts at 1, not: $1"
    seen_page=1
    page="$1"
  }

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
      # The space forms refuse a value that looks like the next flag: a
      # missing value would otherwise swallow it and the complaint would
      # point at the wrong thing. The = spelling takes anything.
      --platform)
        platform="${2:-}"
        shift 2 || die "--platform needs a name"
        [[ -n "$platform" && "$platform" != -* ]] || die "--platform needs a name"
        ;;
      --platform=*)
        platform="${1#--platform=}"
        [[ -n "$platform" ]] || die "--platform needs a name"
        shift
        ;;
      --search)
        [[ -n "${2:-}" && "${2:-}" != --* ]] || die "--search needs a pattern"
        set_pattern "$2"
        shift 2
        ;;
      --search=*)
        [[ -n "${1#--search=}" ]] || die "--search needs a pattern"
        set_pattern "${1#--search=}"
        shift
        ;;
      --page)
        [[ -n "${2:-}" && "${2:-}" != -* ]] || die "--page needs a number"
        set_page "$2"
        shift 2
        ;;
      --page=*)
        set_page "${1#--page=}"
        shift
        ;;
      -*) die "unknown option for list: $1" ;;
      *)
        # A bare number is a page, anything else the pattern — so a number
        # meant as a search wants --search or a regex spelling like '194[2]'.
        if [[ "$1" =~ ^[0-9]+$ ]]; then
          [[ "$1" =~ ^[1-9][0-9]{0,8}$ ]] || die "a page starts at 1, not: $1
     To search for a number, use --search or a regex: gotg list '${1:0:-1}[${1: -1}]'"
          set_page "$1"
        else
          set_pattern "$1"
        fi
        shift
        ;;
    esac
  done
  # bash has no lexically-scoped functions: these two would otherwise linger in
  # the global table, closed over locals that no longer exist.
  unset -f set_pattern set_page
  # No leading zeros: bash arithmetic reads 010 as octal 8, and 08 as an
  # error every (( )) swallows into a false condition. (The page cap that
  # keeps arithmetic clear of 2^63 lives in set_page.)
  [[ "$limit" =~ ^(0|[1-9][0-9]*)$ ]] || die "--limit takes a number, not: $limit"
  if ((limit == 0)) && [[ -n "$page" ]]; then
    die "--all and a page cannot combine — --all is every page at once"
  fi
  : "${page:=1}"

  manifest_ensure
  local rows
  # The entry name comes along so the installed check is an exact path test
  # rather than a guess at what the file is called.
  #
  # The pattern is a regex, matched without case against the id, the title and
  # the platform — the three things anyone would type. `any` rather than three
  # `or`s, because a bare `(.id, .title)` emits one result per field and would
  # print a matching game once per field it matched in.
  # The size is humanized inside jq: one process for the whole catalog, where
  # a $(human_size) per row is a fork per row — seconds over --all.
  if ! rows="$(manifest_games |
    jq -r --arg p "$pattern" --arg plat "${platform,,}" '
      def human: if . < 1024 then "\(.) B"
        elif . < 1048576 then "\(. / 1024 * 10 | round / 10) KB"
        elif . < 1073741824 then "\(. / 1048576 * 10 | round / 10) MB"
        elif . < 1099511627776 then "\(. / 1073741824 * 10 | round / 10) GB"
        else "\(. / 1099511627776 * 10 | round / 10) TB" end;
      select($plat == "" or .platform == $plat)
      | select($p == "" or ([.id, .title, .platform] | any(test($p; "i"))))
      | [.platform, .id, (([.files[].size_bytes] | add) | human), .files[0].name,
         .handler, .title] | @tsv
    ' 2>&1)"; then
    die "not a valid pattern: $pattern
     It is a regex, so a bare . matches any character and ( must be closed."
  fi

  if [[ -z "$rows" ]]; then
    # A platform nobody has is worth naming apart from an unlucky pattern:
    # the answer to one is a different platform, to the other a wider regex.
    if [[ -n "$platform" ]]; then
      local known
      known="$(manifest_games | jq -r '.platform' | sort -u | tr '\n' ' ')"
      if [[ " $known" != *" ${platform,,} "* ]]; then
        log "no games on platform '$platform'"
        log "platforms here: ${known% }"
        return 0
      fi
    fi
    if [[ -n "$pattern" ]]; then
      log "nothing matches $pattern${platform:+ on $platform}"
      return 0
    fi
    log "the catalog is empty"
    return 0
  fi

  local total pages start end
  total="$(wc -l <<<"$rows")"
  pages=1
  start=1
  end="$total"
  if ((limit > 0)); then
    pages=$(((total + limit - 1) / limit))
    if ((page > pages)); then
      log "page $page is past the end — only $pages page(s) ($total game(s))"
      return 0
    fi
    start=$(((page - 1) * limit + 1))
    end=$((page * limit))
    ((end > total)) && end="$total"
    rows="$(sed -n "${start},${end}p" <<<"$rows")"
  fi

  # Built before the render loop: its final read clears the loop variables at
  # EOF, and pattern/platform are about to become row fields.
  # %q, because the hint is made to be pasted back: an unquoted regex like
  # 'filler [0-9]' would come apart into a pattern and a mystery argument.
  local next_cmd="gotg list"
  [[ -n "$pattern" ]] && next_cmd+=" $(printf '%q' "$pattern")"
  [[ -n "$platform" ]] && next_cmd+=" --platform $(printf '%q' "$platform")"
  ((limit > 0 && limit != GOTG_LIST_LIMIT)) && next_cmd+=" --limit $limit"
  next_cmd+=" $((page + 1))"

  # The colour goes in its own argument so the width applies to the value and
  # not to the escape bytes, which would silently break every column.
  local platform id size name handler title status c_status installed_glob
  printf '%s%-3s %-9s %-46s %10s  %s%s\n' \
    "$C_HEAD" "" "PLATFORM" "ID" "SIZE" "TITLE" "$C_RESET"
  while IFS=$'\t' read -r platform id size name handler title; do
    status="[ ]"
    c_status="$C_MUTED"
    # Cache fields become a path even for this read-only probe. A recipe
    # installs as id.<ext>, which the member name cannot predict — the same
    # rule game_installed_path applies, without loading each row's JSON.
    if [[ "$platform" =~ $GOTG_PLATFORM_RE && "$name" != *..* && "$name" != /* ]]; then
      if [[ -e "$GOTG_GAMES_DIR/$platform/$name" || -e "$GOTG_GAMES_DIR/$platform/$id" ]]; then
        status="[*]"
        c_status="$C_OK"
      elif [[ "$handler" != "single_file" && "$handler" != "no_intro_set" ]]; then
        installed_glob=("$GOTG_GAMES_DIR/$platform/$id".*)
        if [[ -e "${installed_glob[0]}" ]]; then
          status="[*]"
          c_status="$C_OK"
        fi
      fi
    fi
    printf '%s%-3s%s %s%-9s%s %s%-46s%s %10s  %s\n' \
      "$c_status" "$status" "$C_RESET" \
      "$C_MUTED" "$platform" "$C_RESET" \
      "$C_ID" "$id" "$C_RESET" \
      "$size" "$title"
  done <<<"$rows"
  log ""
  # Braced: "$C_OK[*]" reads as an array subscript.
  log "${C_OK}[*]${C_RESET} installed locally"

  # Say what was left out, and how to see it. A silent truncation reads as
  # "that is everything", which is the one thing it must not read as.
  if ((pages > 1)); then
    log ""
    log "${C_WARN}showing $start-$end of $total (page $page of $pages).${C_RESET} More:"
    if ((page < pages)); then
      log "  $next_cmd   # the next page"
    fi
    log "  gotg list zelda          # narrow: id, title or platform, as a regex"
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

  # The variant environments this game has — bse, bsmso, 60fps — each
  # reachable as `gotg play <id> <mod>`. Silence when there are none: a
  # "mods: none" line answers a question nobody asked.
  local -a mods=()
  mapfile -t mods < <(env_variant_names \
    "$(manifest_field "$game" platform)" "$(manifest_field "$game" id)")
  if ((${#mods[@]} > 0)); then
    local joined
    printf -v joined '%s, ' "${mods[@]}"
    printf '%smods:     %s %s\n' "$C_MUTED" "$C_RESET" "${joined%, }"
  fi
}
