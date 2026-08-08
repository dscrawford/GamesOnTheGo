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
