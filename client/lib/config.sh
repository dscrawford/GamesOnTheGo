# shellcheck shell=bash
# Server credentials.
#
# The password is stored in a 0600 file rather than a keyring: this runs headless
# under Steam on single-user machines, where a keyring prompt would just hang.
# The JWT is deliberately *not* stored — File Browser's tokens expire in hours,
# so a cached one is a support burden for no gain when logging in again is one
# cheap request.

config_exists() { [[ -f "$GOTG_CONFIG_FILE" ]]; }

# Refuse to read a secret anyone else can read. Takes a path because the config
# is no longer the only such file — api.json holds the GOTG service token, which
# carries both artwork and saves.
config_check_perms() {
  local file="${1:-$GOTG_CONFIG_FILE}" mode
  mode="$(stat -c '%a' "$file")"
  if [[ "${mode: -2}" != "00" ]]; then
    die "$file is mode $mode; it holds a secret. Fix with: chmod 600 $file"
  fi
}

config_get() {
  local key="$1"
  jq -r --arg k "$key" '.[$k] // empty' "$GOTG_CONFIG_FILE"
}

# The service credentials live in api.json now; this file holds only local
# preferences (the flake path). Absent is fine.
config_load() {
  config_exists || return 0
  config_check_perms
}

# Merge keys into the config, keeping everything already there.
#
# This used to rebuild the whole object from its four arguments, which meant a
# second `gotg login` silently dropped every other key — running it after
# `gotg saves setup` would have unconfigured the save backend without saying so.
config_patch() {
  local patch="$1" existing='{}'
  mkdir -p "$GOTG_CONFIG_DIR"
  chmod 700 "$GOTG_CONFIG_DIR"

  if [[ -f "$GOTG_CONFIG_FILE" ]]; then
    config_check_perms "$GOTG_CONFIG_FILE"
    existing="$(cat "$GOTG_CONFIG_FILE")"
    jq -e 'type == "object"' >/dev/null 2>&1 <<<"$existing" ||
      die "$GOTG_CONFIG_FILE is not a JSON object — move it aside and run: gotg login"
  fi

  local tmp="$GOTG_CONFIG_FILE.tmp"
  # Create with the right mode before writing, so the password is never briefly
  # world-readable.
  : >"$tmp"
  chmod 600 "$tmp"
  jq -n --argjson existing "$existing" --argjson patch "$patch" '$existing + $patch' >"$tmp"
  mv "$tmp" "$GOTG_CONFIG_FILE"
}

# One key, for the commands that configure a single thing.
config_set() {
  config_patch "$(jq -nc --arg k "$1" --arg v "$2" '{($k): $v}')"
}

config_write() {
  local server="$1" username="$2" password="$3" remote_root="$4"
  config_patch "$(jq -nc \
    --arg server "$server" \
    --arg username "$username" \
    --arg password "$password" \
    --arg remote_root "$remote_root" \
    '{server: $server, username: $username, password: $password, remote_root: $remote_root}')"
}

prompt_secret() {
  local prompt="$1" value=""
  if is_tty; then
    read -r -s -p "$prompt" value </dev/tty
    printf '\n' >&2
  elif has_display && command -v zenity >/dev/null 2>&1; then
    value="$(zenity --password --title="GOTG" 2>/dev/null)" || true
  else
    die "no terminal or display to prompt for a password"
  fi
  printf '%s' "$value"
}

prompt_line() {
  local prompt="$1" default="${2:-}" value=""
  if is_tty; then
    read -r -p "$prompt" value </dev/tty
  elif has_display && command -v zenity >/dev/null 2>&1; then
    value="$(zenity --entry --title="GOTG" --text="$prompt" 2>/dev/null)" || true
  fi
  printf '%s' "${value:-$default}"
}

# One url, one token: the same api.json that carries saves and artwork now
# carries the whole library. Verified by fetching the catalog before writing,
# so a typo is caught here rather than at launch time.
cmd_login() {
  local url token
  url="$(prompt_line "GOTG service URL [https://gotg-api.dcraw.net]: " "https://gotg-api.dcraw.net")"
  url="${url%/}"
  [[ "$url" == http://* || "$url" == https://* ]] || die "service must be an http(s) URL: $url"
  token="$(prompt_secret "Token: ")"
  [[ -n "$token" ]] || die "a token is required"

  curl -fsS --connect-timeout 10 --max-time 30 \
    -H "Authorization: Bearer $token" "$url/catalog" >/dev/null 2>&1 ||
    die "the service at $url did not accept that token"

  local file tmp
  file="${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}"
  mkdir -p "$(dirname "$file")"
  tmp="$(mktemp "$file.XXXXXX")"
  if [[ -f "$file" ]]; then
    jq --arg url "$url" --arg token "$token" '. + {url: $url, token: $token}' "$file" >"$tmp"
  else
    jq -n --arg url "$url" --arg token "$token" '{url: $url, token: $token}' >"$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$file"
  log "saved $file (mode 600)"

  # The File Browser era left a password behind; a dead credential in a 0600
  # file is still a credential.
  if [[ -f "$GOTG_CONFIG_FILE" ]] && jq -e '.password' "$GOTG_CONFIG_FILE" >/dev/null 2>&1; then
    warn "the old File Browser password in $GOTG_CONFIG_FILE is no longer used;"
    warn "remove it with: jq 'del(.username, .password, .server, .remote_root)' $GOTG_CONFIG_FILE"
  fi
}
