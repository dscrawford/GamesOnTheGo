# shellcheck shell=bash
# Server credentials.
#
# The password is stored in a 0600 file rather than a keyring: this runs headless
# under Steam on single-user machines, where a keyring prompt would just hang.
# The JWT is deliberately *not* stored — File Browser's tokens expire in hours,
# so a cached one is a support burden for no gain when logging in again is one
# cheap request.

config_exists() { [[ -f "$GOTG_CONFIG_FILE" ]]; }

# A token over cleartext is a token given away, and this one does not expire.
# Loopback is exempt: no network to read there, and the tests run on it.
url_is_private_or_tls() {
  case "$1" in
    https://*) return 0 ;;
    http://127.0.0.1 | http://127.0.0.1:* | http://127.0.0.1/*) return 0 ;;
    http://localhost | http://localhost:* | http://localhost/*) return 0 ;;
    http://\[::1\] | http://\[::1\]:* | http://\[::1\]/*) return 0 ;;
    *) return 1 ;;
  esac
}

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
  local url token name=""
  if [[ "${1:-}" == "--claim" ]]; then
    local claim="${2:-}"
    [[ -n "$claim" ]] || die "usage: gotg login [--claim <url>]"
    claim="${claim%/}"
    [[ "$claim" == http://*/claim/* || "$claim" == https://*/claim/* ]] ||
      die "not a claim url: $claim"
    url="${claim%%/claim/*}"
    url_is_private_or_tls "$url" ||
      die "that claim link is http, which hands the token to the network: $url"
    local code="${claim##*/claim/}"
    # A link that has been through a chat client arrives with tracking on it.
    code="${code%%\?*}"
    [[ "$code" =~ ^gotgi_[A-Za-z0-9_-]{40,50}$ ]] || die "not a claim url: $claim"

    # One POST, one token: the reply is the only time the plaintext exists
    # outside the config file about to be written.
    local reply http
    reply="$(mktemp)"
    # Expanded now on purpose: the path is gone by trap time. EXIT too, since
    # `die` exits without returning and would leave the plaintext in /tmp.
  # shellcheck disable=SC2064
    trap "rm -f '$reply'" RETURN EXIT
    # The code rides the body, not the URL: every log between here and the
    # service keeps the request line, and this is live until spent. printf is
    # a builtin, so unlike jq the code never reaches a world-readable cmdline.
    http="$(printf '{"code":"%s"}' "$code" |
      curl -sS -o "$reply" -w '%{http_code}' --connect-timeout 10 --max-time 30 \
        -X POST --data-binary @- "$url/claim")" || die "could not reach $url"
    if [[ "$http" != 200 ]]; then
      die "claim failed: $(printable "$(jq -r '.error // "the service answered '"$http"'"' "$reply" 2>/dev/null)")"
    fi
    token="$(jq -r '.token // empty' "$reply")"
    name="$(printable "$(jq -r '.name // empty' "$reply")")"
    [[ -n "$token" ]] || die "the claim reply carried no token"
  else
    url="$(prompt_line "GOTG service URL [https://gotg-api.dcraw.net]: " "https://gotg-api.dcraw.net")"
    url="${url%/}"
    [[ "$url" == http://* || "$url" == https://* ]] || die "service must be an http(s) URL: $url"
    url_is_private_or_tls "$url" ||
      die "that url is http, which sends the token in the clear: $url"
    token="$(prompt_secret "Token: ")"
    [[ -n "$token" ]] || die "a token is required"
  fi
  # The shape every bearer token has; anything else would also corrupt the
  # curl config the token is spliced into.
  [[ "$token" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || die "token contains characters no bearer token uses"

  # Via curl --config on stdin, never argv: /proc/<pid>/cmdline is
  # world-readable and this token does not expire. whoami is served by every
  # pod; /catalog only by the library — and a claimed token deserves a check
  # of the machinery that minted it.
  local probe="/catalog"
  [[ -z "$name" ]] || probe="/auth/whoami"
  printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl --config - -fsS --connect-timeout 10 --max-time 30 "$url$probe" >/dev/null 2>&1 ||
    die "the service at $url did not accept that token"

  local file tmp
  file="${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}"
  mkdir -p "$(dirname "$file")"
  tmp="$(mktemp "$file.XXXXXX")"
  if [[ -f "$file" ]]; then
    jq --arg url "$url" --arg token "$token" --arg name "$name" \
      '. + {url: $url, token: $token} + (if $name != "" then {name: $name} else {} end)' "$file" >"$tmp"
  else
    jq -n --arg url "$url" --arg token "$token" --arg name "$name" \
      '{url: $url, token: $token} + (if $name != "" then {name: $name} else {} end)' >"$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$file"
  log "saved $file (mode 600)${name:+ — you are $name}"

  # The File Browser era left a password behind; a dead credential in a 0600
  # file is still a credential.
  if [[ -f "$GOTG_CONFIG_FILE" ]] && jq -e '.password' "$GOTG_CONFIG_FILE" >/dev/null 2>&1; then
    warn "the old File Browser password in $GOTG_CONFIG_FILE is no longer used;"
    warn "remove it with: jq 'del(.username, .password, .server, .remote_root)' $GOTG_CONFIG_FILE"
  fi
}
