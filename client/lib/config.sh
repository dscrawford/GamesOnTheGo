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
# is no longer the only such file — an rclone.conf holds OAuth refresh tokens,
# which are worth rather more than one library password.
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

config_load() {
  config_exists || die "not configured yet — run: gotg login"
  config_check_perms
  GOTG_SERVER="$(config_get server)"
  GOTG_USER="$(config_get username)"
  GOTG_PASS="$(config_get password)"
  GOTG_REMOTE_ROOT="$(config_get remote_root)"
  : "${GOTG_REMOTE_ROOT:=/Games}"
  [[ -n "$GOTG_SERVER" ]] || die "no server in $GOTG_CONFIG_FILE — run: gotg login"
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

cmd_login() {
  local server username password
  server="$(prompt_line "File Browser URL [https://downloads.dcraw.net]: " "https://downloads.dcraw.net")"
  server="${server%/}"
  [[ "$server" == http://* || "$server" == https://* ]] || die "server must be an http(s) URL: $server"
  username="$(prompt_line "Username: ")"
  [[ -n "$username" ]] || die "username is required"
  password="$(prompt_secret "Password: ")"
  [[ -n "$password" ]] || die "password is required"

  local remote_root
  remote_root="$(prompt_line "Library path on the server [/Games]: " "/Games")"
  validate_remote_path "$remote_root"

  # Verify before saving, so a typo is caught now rather than at launch time.
  # api_login reads these three.
  # shellcheck disable=SC2034
  {
    GOTG_SERVER="$server"
    GOTG_USER="$username"
    GOTG_PASS="$password"
  }
  local token
  token="$(api_login)" || die "login failed"
  [[ -n "$token" ]] || die "login failed: no token returned"

  config_write "$server" "$username" "$password" "$remote_root"
  log "saved $GOTG_CONFIG_FILE (mode 600)"
  log "logged in to $server as $username"
}
