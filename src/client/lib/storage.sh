# shellcheck shell=bash
# Where games are kept: a list of directories — a handheld's disk and then its
# SD card. Every one is searched for what is installed; the first is where the
# next download lands. No quota: the device's free space is the limit.

# The directories, one per line, first is the default. GOTG_GAMES_DIRS wins
# (a colon-separated list, for a caller that owns the whole layout), then an
# explicit GOTG_GAMES_DIR, then the config, then ~/Games.
storage_dirs() {
  if [[ -n "${GOTG_GAMES_DIRS:-}" ]]; then
    tr ':' '\n' <<<"$GOTG_GAMES_DIRS" | grep -v '^$'
    return 0
  fi
  if [[ -n "$GOTG_GAMES_DIR_EXPLICIT" ]]; then
    printf '%s\n' "$GOTG_GAMES_DIR"
    return 0
  fi
  local configured
  configured="$(storage_configured)"
  if [[ -n "$configured" ]]; then
    printf '%s\n' "$configured"
  else
    printf '%s\n' "$GOTG_GAMES_DIR"
  fi
}

# The list as the config holds it, one per line; nothing when unset. Only
# absolute paths are kept: the file is the person's own, but a relative entry
# would resolve against whatever directory a launcher happened to start in.
storage_configured() {
  # Read directly, not through config_load: this runs before every command,
  # completion included, and the file holds no secret to refuse over.
  config_exists || return 0
  jq -r '(.games_dirs // []) | .[] | select(type == "string" and startswith("/"))' \
    "$GOTG_CONFIG_FILE" 2>/dev/null || true
}

# Point the default at the configured first directory. Called once at startup,
# after the config is loaded and before anything names a path.
# shellcheck disable=SC2034 # both are read by every other lib
storage_init() {
  local first
  first="$(storage_dirs | head -n1)"
  [[ -n "$first" ]] || return 0
  GOTG_GAMES_DIR="$first"
  [[ -n "${GOTG_PARTIAL_DIR_EXPLICIT:-}" ]] || GOTG_PARTIAL_DIR="$GOTG_GAMES_DIR/.gotg-partial"
}

# Free and total bytes on the device holding a directory, as "free total";
# nothing for a directory that is not there.
storage_space() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  df -P -B1 -- "$dir" 2>/dev/null | awk 'NR == 2 { print $4, $2 }'
}

# The games directory an installed game is under, or the default when it is
# not here: launchers and removals happen where the game is.
game_games_dir() {
  local game="$1" root
  while IFS= read -r root; do
    [[ -n "$root" ]] || continue
    if _game_installed_in "$game" "$root" >/dev/null; then
      printf '%s' "$root"
      return 0
    fi
  done < <(storage_dirs)
  printf '%s' "$GOTG_GAMES_DIR"
}

_storage_write() {
  local dirs_json="$1"
  config_patch "$(jq -nc --argjson d "$dirs_json" '{games_dirs: $d}')"
}

# A path as the config should hold it: absolute, resolved, existing and
# writable — created if it is not there yet, since the whole point of adding
# one is usually a fresh card.
_storage_resolve() {
  local path="$1" resolved
  [[ -n "$path" ]] || die "usage: gotg configure storage add <directory>"
  [[ "$path" == /* ]] || die "a games directory must be an absolute path: $path"
  resolved="$(realpath -m -- "$path")"
  mkdir -p -- "$resolved" 2>/dev/null || die "cannot create $resolved"
  [[ -w "$resolved" ]] || die "not writable: $resolved"
  printf '%s' "$resolved"
}

# The list with one path first, the rest in their order, no repeats.
_storage_reorder() {
  local first="$1"
  jq -nc --arg first "$first" --args '[$first] + ($ARGS.positional | map(select(. != $first)))' "${@:2}"
}

storage_add() {
  local resolved
  resolved="$(_storage_resolve "${1:-}")"
  local current=()
  mapfile -t current < <(storage_dirs)
  for d in "${current[@]}"; do
    [[ "$d" != "$resolved" ]] || die "already a games directory: $resolved"
  done
  # The first addition keeps what was the only directory: adding a card must
  # not make the games already on the disk invisible.
  _storage_write "$(jq -nc --args '$ARGS.positional' "${current[@]}" "$resolved")"
  log "added: $resolved"
  log "downloads still land in $(storage_dirs | head -n1); to change that: gotg configure storage default $resolved"
}

storage_remove() {
  local path="${1:-}" resolved
  [[ -n "$path" ]] || die "usage: gotg configure storage remove <directory>"
  [[ "$path" == /* ]] || die "a games directory must be an absolute path: $path"
  resolved="$(realpath -m -- "$path")"
  local current=() kept=()
  mapfile -t current < <(storage_dirs)
  for d in "${current[@]}"; do
    [[ "$d" == "$resolved" ]] || kept+=("$d")
  done
  [[ ${#kept[@]} -lt ${#current[@]} ]] || die "not a games directory: $resolved"
  [[ ${#kept[@]} -gt 0 ]] || die "refusing to remove the last games directory"
  _storage_write "$(jq -nc --args '$ARGS.positional' "${kept[@]}")"
  log "forgot: $resolved — nothing in it was deleted"
}

storage_default() {
  local resolved
  resolved="$(_storage_resolve "${1:-}")"
  local current=()
  mapfile -t current < <(storage_dirs)
  _storage_write "$(_storage_reorder "$resolved" "${current[@]}")"
  log "downloads now land in $resolved"
}

# One row per directory: whether it is the default, whether it exists, and
# the device's free and total bytes. --json is what the UI reads.
storage_list() {
  local json="" root first=1 space free total
  [[ "${1:-}" != "--json" ]] || json=1
  local rows=()
  while IFS= read -r root; do
    [[ -n "$root" ]] || continue
    free=0 total=0
    if space="$(storage_space "$root")"; then
      read -r free total <<<"$space"
    fi
    if [[ -n "$json" ]]; then
      rows+=("$(jq -nc --arg p "$root" --argjson d "$first" --argjson e "$([[ -d "$root" ]] && echo true || echo false)" \
        --argjson f "$free" --argjson t "$total" \
        '{path: $p, default: ($d == 1), exists: $e, free_bytes: $f, total_bytes: $t}')")
    else
      local mark=" " note=""
      [[ "$first" -eq 1 ]] && mark="*"
      if [[ ! -d "$root" ]]; then
        note="(missing)"
      else
        note="$(human_size "$free") free of $(human_size "$total")"
      fi
      printf '%s %s  %s\n' "$mark" "$root" "$note"
    fi
    first=0
  done < <(storage_dirs)
  if [[ -n "$json" ]]; then
    printf '%s\n' "${rows[@]+"${rows[@]}"}" | jq -s '.'
  else
    log ""
    log "* is where downloads land; every directory is searched for installed games."
  fi
}

cmd_configure_storage() {
  local sub="${1:-list}"
  [[ $# -gt 0 ]] && shift || true
  case "$sub" in
    list) storage_list "$@" ;;
    add) storage_add "$@" ;;
    remove | forget) storage_remove "$@" ;;
    default) storage_default "$@" ;;
    -h | --help | help)
      cat <<'EOF'
usage: gotg configure storage [list [--json] | add <dir> | remove <dir> | default <dir>]

  Where games are kept. Every directory is searched for installed games; the
  first (*) is where downloads land. Removing a directory forgets it and
  deletes nothing. No quota: the device's own free space is the limit.
EOF
      ;;
    *) die "usage: gotg configure storage [list | add <dir> | remove <dir> | default <dir>]" ;;
  esac
}
