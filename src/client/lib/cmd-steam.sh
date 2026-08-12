# shellcheck shell=bash
# `gotg steam` — putting a game in Steam without the file picker.
#
# `install` writes a launcher and tells you to add it by hand: Games → Add a
# Non-Steam Game → Browse, change the filter to All Files, find the script,
# rename the entry. That is six steps to run one command, and it has to be
# repeated per variant.
#
# Steam keeps its non-Steam games in a binary shortcuts.vdf, so the file itself
# is handled by client/steam/shortcuts.py. What is here is everything around it:
# which game, which launcher, which Steam account, and the one rule that makes
# the difference between working and silently doing nothing —
#
#   STEAM REWRITES THAT FILE WHEN IT EXITS. Anything written while it is running
#   is discarded without a word. So this refuses to touch it while Steam is up,
#   which is the whole reason it is a command and not a line in the README.

steam_usage() {
  cat <<'EOF'
usage: gotg steam <command> [args]

  add <id> [variant]     put it in Steam, writing the launcher if needed
  remove <id> [variant]  take it out again
  art <id> [variant]     fetch its artwork again, --force to replace
                         --from <file|url> to choose the picture yourself
  list                   every non-Steam game Steam knows about

Steam must be closed: it rewrites its shortcut file on exit, so a change made
while it is running is thrown away.
EOF
}

cmd_steam() {
  local verb="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  case "$verb" in
    add) steam_add "$@" ;;
    remove | rm) steam_remove "$@" ;;
    art | artwork) steam_art "$@" ;;
    list | ls) steam_list "$@" ;;
    help | --help | -h | "") steam_usage ;;
    *)
      printf 'error: unknown steam command: %s\n\n' "$verb" >&2
      steam_usage >&2
      exit 1
      ;;
  esac
}

# Steam's per-account directory. Flatpak and the native package put it in
# different places, and an account that has never had a non-Steam game has no
# shortcuts.vdf yet — which is a file to create, not an error.
steam_shortcuts_file() {
  # Overridable so the tests can point at a file of their own rather than at a
  # real Steam library.
  if [[ -n "${GOTG_STEAM_SHORTCUTS:-}" ]]; then
    printf '%s' "$GOTG_STEAM_SHORTCUTS"
    return 0
  fi

  local root candidates=(
    "$HOME/.local/share/Steam"
    "$HOME/.steam/steam"
    "$HOME/.var/app/com.valvesoftware.Steam/data/Steam"
  )
  for root in "${candidates[@]}"; do
    [[ -d "$root/userdata" ]] || continue
    local user users=()
    for user in "$root"/userdata/*/; do
      [[ -d "$user" ]] || continue
      [[ "$(basename "$user")" == "0" ]] && continue
      users+=("$user")
    done
    ((${#users[@]} > 0)) || continue
    if ((${#users[@]} > 1)); then
      warn "several Steam accounts here; using $(basename "${users[0]}")"
    fi
    printf '%sconfig/shortcuts.vdf' "${users[0]}"
    return 0
  done
  die "no Steam account found. Looked in ${candidates[*]}"
}

# Steam discards changes made behind its back, so this is a hard stop rather
# than a warning: carrying on would report success and change nothing.
steam_require_closed() {
  # The guard exists to protect Steam's own file. Pointed at another one — which
  # is what GOTG_STEAM_SHORTCUTS is for — there is nothing for Steam to
  # overwrite, so whether it is running stops mattering.
  [[ -z "${GOTG_STEAM_SHORTCUTS:-}" ]] || return 0
  pgrep -x steam >/dev/null 2>&1 || return 0
  die "Steam is running, and it overwrites its shortcuts when it exits.
     Close Steam, run this again, then start it."
}

# Invoked through python rather than by its shebang: /usr/bin/env does not
# exist in a nix build sandbox, so `#!/usr/bin/env python3` fails there — which
# is exactly where the tests run. The python on PATH is the package's own, with
# the vdf module in it.
steam_helper() {
  python3 "${GOTG_STEAM_HELPER:-$GOTG_ROOT/steam/shortcuts.py}" "$@"
}

steam_artwork_helper() {
  python3 "${GOTG_STEAM_ARTWORK:-$GOTG_ROOT/steam/artwork.py}" "$@"
}

# Where Steam keeps a non-Steam game's pictures: beside the shortcuts, keyed by
# the appid the shortcut carries.
steam_grid_dir() { printf '%s/grid' "$(dirname "$(steam_shortcuts_file)")"; }

# The fetched icon's path: grid art is keyed on the unsigned 32-bit appid,
# whichever way the shortcut spelled it. The vdf is another tool's to write
# too, so what came out of it is validated before bash arithmetic sees it.
steam_icon_path() {
  local appid="$1"
  [[ "$appid" =~ ^-?[0-9]+$ ]] || return 0
  ((appid < 0)) && appid=$((appid + 4294967296))
  printf '%s/%s_icon.ico' "$(steam_grid_dir)" "$appid"
}

# Point the shortcut's own icon field at the fetched icon. Grid art Steam
# finds by filename; the list icon it reads only off the shortcut — so a
# downloaded _icon.ico does nothing until this runs. Best-effort throughout:
# the vdf write needs Steam closed, and artwork must never take the shortcut
# with it. set-icon rather than add, because add recomputes appid and AppName
# — a rename made inside Steam, or the random appid of an entry Steam itself
# created, must survive having a picture attached.
steam_attach_icon() {
  local launcher="$1" icon="$2"
  [[ -n "$icon" && -f "$icon" ]] || return 0

  local current
  current="$(steam_helper --file "$(steam_shortcuts_file)" list |
    jq -r --arg e "$launcher" '.[] | select(.exe == $e) | .icon')" || {
    warn "could not read the shortcut list — the icon stays unattached"
    return 0
  }
  [[ "$current" != "$icon" ]] || return 0

  if [[ -z "${GOTG_STEAM_SHORTCUTS:-}" ]] && pgrep -x steam >/dev/null 2>&1; then
    log "the icon is fetched but Steam is running — close it and rerun to attach it"
    return 0
  fi
  steam_helper --file "$(steam_shortcuts_file)" set-icon \
    --exe "$launcher" --icon "$icon" >/dev/null ||
    warn "could not attach the icon to the shortcut"
}

# The SteamGridDB key. Its own file, like the controller order: it is a
# credential, but not the server password, and it is the one part of this that
# cannot be automated — an unauthenticated request to their API is a 401.
steam_api_key() {
  local file="${GOTG_STEAMGRIDDB_KEY_FILE:-$GOTG_CONFIG_DIR/steamgriddb.json}"
  [[ -f "$file" ]] || return 0
  jq -r '.api_key // empty' "$file" 2>/dev/null || true
}

# The cluster proxy, if this machine has been pointed at one.
#
# It holds the real keys and swaps them in, so a client configured this way
# needs no SteamGridDB key of its own — which is the whole reason it exists.
# One token for our own service beats a key for somebody else's on every laptop
# and Steam Deck, and rotating it is one kubectl command rather than a tour of
# the house.
steam_api_file() { printf '%s' "${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}"; }
steam_api_field() {
  local file
  file="$(steam_api_file)"
  [[ -f "$file" ]] || return 1
  jq -re --arg f "$1" '.[$f] // empty' "$file" 2>/dev/null
}

# It holds a bearer token for a service on the public internet, so it is checked
# the same way the server credentials are — a token anyone on the machine can
# read is a token anyone on the machine can spend.
#
# Called here rather than from steam_api_field, which is only ever run inside a
# command substitution: a die in there ends the subshell and nothing else, so
# the refusal was swallowed by the caller and the token read anyway. The check
# has to happen where it can actually stop the command.
steam_check_api_perms() {
  local file
  file="$(steam_api_file)"
  [[ ! -f "$file" ]] || config_check_perms "$file"
}

# Best-effort by design: a shortcut with no picture is a working shortcut.
#
# Two sources. SteamGridDB is better when it has the game, but needs a key;
# libretro-thumbnails needs nothing and is keyed by No-Intro names, so it runs
# whether or not there is a key and fills whatever the first source left. That
# is why a missing key is a note rather than a reason to stop.
steam_fetch_artwork() {
  local name="$1" appid="$2" game="$3"
  shift 3

  # Overridable so the tests can point at a stand-in rather than the real
  # service, the same seam GOTG_STEAM_SHORTCUTS provides for Steam's own file.
  local base=() lr=() key say_key="no"
  [[ -z "${GOTG_LIBRETRO_URL:-}" ]] || lr=(--libretro-url "$GOTG_LIBRETRO_URL")

  steam_check_api_perms

  local proxy="" proxy_token=""
  proxy="$(steam_api_field url)" || proxy=""
  proxy_token="$(steam_api_field token)" || proxy_token=""

  if [[ -n "${GOTG_STEAMGRIDDB_URL:-}" ]]; then
    # An explicit override wins over everything, including the proxy.
    base=(--base-url "$GOTG_STEAMGRIDDB_URL")
    key="$(steam_api_key)"
    [[ -z "$key" ]] || base+=(--api-key "$key")
    [[ -n "$key" ]] || say_key="yes"
  elif [[ -n "$proxy" && -n "$proxy_token" ]]; then
    # The client sends its own token; the proxy swaps in the real key. Nothing
    # about the helper changes — it is the same API under a prefix.
    base=(--base-url "$proxy/steamgriddb" --api-key "$proxy_token")
  else
    key="$(steam_api_key)"
    if [[ -n "$key" ]]; then
      base=(--api-key "$key")
    else
      say_key="yes"
    fi
  fi

  local result rc=0
  result="$(steam_artwork_helper --grid-dir "$(steam_grid_dir)" \
    --appid "$appid" --name "$name" \
    --id "$(manifest_field "$game" id)" \
    --platform "$(manifest_field "$game" platform)" \
    --playlists "$GOTG_DATA/libretro-playlists.json" \
    --cache-dir "$GOTG_STATE_DIR/libretro" \
    "${base[@]}" "${lr[@]}" "$@")" || rc=$?

  # A picture named by hand is the one case that is not best-effort: somebody
  # typed a path, so a path that cannot be used is an error rather than a
  # warning to read past.
  local problem
  problem="$(jq -r '.error // empty' <<<"$result" 2>/dev/null || true)"
  [[ -z "$problem" ]] || die "$problem"
  ((rc == 0)) || {
    warn "artwork fetch failed; the shortcut is fine"
    return 0
  }

  local wrote
  wrote="$(jq -r '.wrote // empty' <<<"$result")"
  if [[ -n "$wrote" ]]; then
    log "artwork: $wrote, from $(jq -r '.source' <<<"$result")"
    return 0
  fi

  local got lr_got kept
  got="$(jq -r '.downloaded | length' <<<"$result")"
  lr_got="$(jq -r '.libretro.downloaded | length' <<<"$result")"
  # Counted, because "everything is already there" and "neither source had
  # anything" both download nothing and are not the same news.
  kept="$(jq -r '(.kept | length) + (.libretro.kept | length)' <<<"$result")"

  if ((got == 0 && lr_got == 0 && kept == 0)); then
    warn "no artwork: $(jq -r '[.skipped, .libretro.skipped] | map(select(.)) | join("; ")' <<<"$result")"
  else
    log "artwork: $((got + lr_got)) file(s)$(jq -r 'if .game then " for \(.game)" else "" end' <<<"$result")"
    ((lr_got == 0)) || log "  $lr_got from libretro-thumbnails"
  fi

  # Said once, at the end, and only when it would have made a difference.
  if [[ "$say_key" == "yes" ]]; then
    log ""
    log "No SteamGridDB key. libretro-thumbnails needs none and was used instead;"
    log "for Switch games, which it has none of, either point at the cluster"
    log "proxy, which holds the key for every machine:"
    log "  echo '{\"url\": \"https://gotg-api.dcraw.net\", \"token\": \"...\"}' > $(steam_api_file)"
    log "or give this machine its own key:"
    log "  https://www.steamgriddb.com/profile/preferences/api"
    log "  echo '{\"api_key\": \"...\"}' > $GOTG_CONFIG_DIR/steamgriddb.json"
    log "  gotg steam art <id> --force"
  fi
}

steam_art() {
  local want="${1:-}" variant="" opts=()
  [[ -n "$want" ]] || die "usage: gotg steam art <id> [variant] [--force] [--from <file|url> [--as tile|capsule|hero|logo|icon]]"
  shift
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi
  # Passed through rather than enumerated: --from and --as belong to the
  # helper, which is where their meaning and their error messages live.
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force)
        opts+=(--force)
        shift
        ;;
      --from | --as)
        [[ -n "${2:-}" ]] || die "$1 needs a value"
        opts+=("$1" "$2")
        shift 2
        ;;
      *) die "unknown option for steam art: $1" ;;
    esac
  done

  manifest_ensure
  local game launcher name appid
  game="$(manifest_find "$want")"
  launcher="$(launcher_path "$game" "$variant")"
  name="$(sanitize_title "$(manifest_field "$game" title)")"
  [[ -z "$variant" ]] || name="$name ($variant)"

  # The id Steam files artwork under is the one on the shortcut, so it is read
  # back rather than recomputed — if the two ever disagree, the shortcut wins.
  appid="$(steam_helper --file "$(steam_shortcuts_file)" list |
    jq -r --arg e "$launcher" '.[] | select(.exe == $e) | .appid')"
  [[ -n "$appid" ]] || die "$name is not in Steam yet — run: gotg steam add $want${variant:+ $variant}"

  steam_fetch_artwork "$name" "$appid" "$game" "${opts[@]}"
  steam_attach_icon "$launcher" "$(steam_icon_path "$appid")"
}

steam_add() {
  local want="${1:-}" variant=""
  [[ -n "$want" ]] || die "usage: gotg steam add <id> [variant]"
  shift
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  steam_require_closed
  manifest_ensure

  local game launcher
  game="$(manifest_find "$want")"
  # env_attr validates the variant, so a typo is caught here rather than
  # becoming a Steam entry that fails at launch.
  env_attr "$game" "$variant" >/dev/null

  launcher="$(launcher_path "$game" "$variant")"
  [[ -f "$launcher" ]] || launcher="$(launcher_write "$game" "$variant")"

  local name
  name="$(sanitize_title "$(manifest_field "$game" title)")"
  [[ -z "$variant" ]] || name="$name ($variant)"

  local result
  result="$(steam_helper --file "$(steam_shortcuts_file)" add \
    --name "$name" --exe "$launcher" --start-dir "$(dirname "$launcher")" \
    --tag "$(manifest_field "$game" platform)")" ||
    die "could not write the Steam shortcut"

  log "$(jq -r '"\(.action): \(.name)"' <<<"$result")"
  log "  $launcher"

  local new_appid
  new_appid="$(jq -r '.appid' <<<"$result")"
  steam_fetch_artwork "$name" "$new_appid" "$game"
  steam_attach_icon "$launcher" "$(steam_icon_path "$new_appid")"

  log ""
  log "Start Steam and it will be in your library."
}

steam_remove() {
  local want="${1:-}" variant=""
  [[ -n "$want" ]] || die "usage: gotg steam remove <id> [variant]"
  shift
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  steam_require_closed
  manifest_ensure

  local game launcher result
  game="$(manifest_find "$want")"
  launcher="$(launcher_path "$game" "$variant")"

  result="$(steam_helper --file "$(steam_shortcuts_file)" remove --exe "$launcher")" ||
    die "could not rewrite the Steam shortcuts"

  case "$(jq -r '.action' <<<"$result")" in
    removed) log "removed: $(jq -r '.name' <<<"$result")" ;;
    *) log "not in Steam: $launcher" ;;
  esac
}

steam_list() {
  local file result
  file="$(steam_shortcuts_file)"
  result="$(steam_helper --file "$file" list)" || die "could not read $file"

  if [[ "$(jq 'length' <<<"$result")" == "0" ]]; then
    log "no non-Steam games."
    return 0
  fi
  jq -r --arg h "$C_HEAD" --arg m "$C_MUTED" --arg r "$C_RESET" \
    '.[] | "\($h)\(.name)\($r)\n  \($m)\(.exe)\($r)"' <<<"$result" >&2
}
