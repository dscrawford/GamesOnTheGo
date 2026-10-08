# shellcheck shell=bash
# The local config file (preferences such as the library and games_dirs) and
# the permission check shared with the credential files beside it.
#
# The service credentials are not here: `gotg login` writes api.json, and this
# file never holds a secret of its own. Kept out of a keyring on purpose --
# this runs headless under Steam on single-user machines, where a keyring
# prompt would just hang -- so a secret is a 0600 file.

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
# second `gotg login` silently dropped every other key and would have
# unconfigured the save backend without saying so.
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

  # Mode 600 before the first byte, so it is never briefly world-readable.
  json_merge_file "$GOTG_CONFIG_FILE" "$patch" 600
}

prompt_secret() {
  local prompt="$1" value=""
  if is_tty; then
    read -r -s -p "$prompt" value </dev/tty
    printf '\n' >&2
  elif has_display && have_zenity; then
    value="$(zenity_run --password --title="GOTG" 2>/dev/null)" || true
  elif [[ ! -t 0 ]]; then
    # Piped: a script handing a token over, the one way that never shows it.
    IFS= read -r value || true
  else
    die "no terminal or display to prompt for a password"
  fi
  printf '%s' "$value"
}

prompt_line() {
  local prompt="$1" default="${2:-}" value=""
  if is_tty; then
    read -r -p "$prompt" value </dev/tty
  elif has_display && have_zenity; then
    value="$(zenity_run --entry --title="GOTG" --text="$prompt" 2>/dev/null)" || true
  else
    # As prompt_secret already does. Nowhere to ask means the answer is not
    # known — silently taking the default would point a headless login at a
    # server nobody chose.
    die "no terminal or display to prompt for: $prompt"
  fi
  printf '%s' "${value:-$default}"
}

# One url, one token: the same api.json that carries saves and artwork now
# carries the whole library. Verified by fetching the catalog before writing,
# so a typo is caught here rather than at launch time.
# The token for Nix. A library flake pins the catalog as an input
# (docs/nix-games.md), and Nix authenticates a fetch only through a netrc file
# -- the service takes the token as the Basic password on its catalog. So a
# netrc beside api.json, 0600, with this server's host in it; and Nix pointed
# at it, unless Nix already reads a netrc of its own, whose other credentials
# a second file would hide: then the line to add is said, never the token.
login_netrc() {
  local url="$1" token="$2" host netrc conf system current
  host="${url#*://}"
  host="${host%%/*}"
  host="${host%%:*}"
  [[ "$host" =~ ^[A-Za-z0-9.-]+$ ]] || {
    warn "not writing a netrc for $url: no host Nix could match"
    return 0
  }
  netrc="$GOTG_CONFIG_DIR/netrc"
  {
    [[ ! -f "$netrc" ]] || grep -v "^machine $host " "$netrc" || true
    printf 'machine %s login gotg password %s\n' "$host" "$token"
  } | atomic_write "$netrc" 600

  conf="${XDG_CONFIG_HOME:-$HOME/.config}/nix/nix.conf"
  system="${GOTG_SYSTEM_NETRC:-/etc/nix/netrc}"
  if [[ -f "$conf" ]] && grep -qE '^[[:space:]]*netrc-file[[:space:]]*=' "$conf"; then
    current="$(sed -nE 's/^[[:space:]]*netrc-file[[:space:]]*=[[:space:]]*//p' "$conf" | tail -n1)"
    [[ "$current" == "$netrc" ]] && return 0
    warn "Nix reads its netrc from $current. For a library flake's catalog, add to it:"
    warn "  machine $host login gotg password <the token in $netrc>"
    return 0
  fi
  if [[ -e "$system" ]]; then
    warn "Nix reads its netrc from $system. For a library flake's catalog, add to it (as root):"
    warn "  machine $host login gotg password <the token in $netrc>"
    return 0
  fi
  # A nix.conf that is a symlink is somebody's generated file -- Home
  # Manager's, into the read-only store -- and appending to it failed the
  # whole login after the claim was spent. Its source is where this goes.
  if [[ -L "$conf" || (-e "$conf" && ! -w "$conf") ]]; then
    warn "$conf is generated (Home Manager?). For a library flake's catalog, add to its nix settings:"
    warn "  netrc-file = $netrc"
    return 0
  fi
  mkdir -p "$(dirname "$conf")"
  printf 'netrc-file = %s\n' "$netrc" >>"$conf"
  log "Nix will fetch the catalog with $netrc (netrc-file, in $conf)"
}

# Whether Nix already reads a netrc of its own: one this user's nix.conf
# names, or the system's.
nix_reads_a_netrc() {
  local conf="${XDG_CONFIG_HOME:-$HOME/.config}/nix/nix.conf"
  if [[ -f "$conf" ]] && grep -qE '^[[:space:]]*netrc-file[[:space:]]*=' "$conf"; then
    return 0
  fi
  [[ -e "${GOTG_SYSTEM_NETRC:-/etc/nix/netrc}" ]]
}

# Every nix this client runs is handed the netrc login keeps, through
# NIX_CONFIG. Writing nix.conf at login was the only way it ever reached Nix,
# and a nix.conf Home Manager generates cannot be written: the library's
# build then died on the catalog's 401 with the token sitting beside it.
# Not where Nix has a netrc of its own, whose other credentials ours would
# hide; login says there what to add to it.
nix_hand_netrc() {
  local netrc="$GOTG_CONFIG_DIR/netrc"
  [[ -s "$netrc" ]] || return 0
  [[ "${NIX_CONFIG:-}" != *netrc-file* ]] || return 0
  nix_reads_a_netrc && return 0
  export NIX_CONFIG="netrc-file = $netrc${NIX_CONFIG:+
$NIX_CONFIG}"
}

# `gotg login` is a sequence of steps; they share the answer to "who, where,
# with what" through these globals, set by the step named:
#
#   LOGIN_SERVER   the --server given             (_login_parse_args)
#   LOGIN_CLAIM    the --claim given, trailing / stripped (_login_parse_args)
#   LOGIN_URL      the service, no trailing /      (_login_claim_parts, _login_ask)
#   LOGIN_CODE     the claim code from the link    (_login_claim_parts)
#   LOGIN_TOKEN    the bearer token                (_login_claim_exchange, _login_ask)
#   LOGIN_NAME     whose token it is, claims only  (_login_claim_exchange)

# The arguments: `nix run <library>#login` passes --server, because
# the library knows its server and the one question left is the token.
_login_parse_args() {
  LOGIN_SERVER="" LOGIN_CLAIM="" LOGIN_URL="" LOGIN_CODE="" LOGIN_TOKEN="" LOGIN_NAME=""
  if [[ "${1:-}" == "--server" ]]; then
    LOGIN_SERVER="${2:-}"
    [[ -n "$LOGIN_SERVER" ]] || die "usage: gotg login [--claim <url>] [--server <url>]"
    shift 2
  fi
  if [[ "${1:-}" == "--claim" ]]; then
    LOGIN_CLAIM="${2:-}"
    [[ -n "$LOGIN_CLAIM" ]] || die "usage: gotg login [--claim <url>]"
    LOGIN_CLAIM="${LOGIN_CLAIM%/}"
  fi
}

# A claim link taken apart and refused unless it is one: the service
# URL (which must be private or TLS, or the token crosses the network) and the
# code. Sets LOGIN_URL and LOGIN_CODE. $1 the link.
_login_claim_parts() {
  local claim="$1"
  [[ "$claim" == http://*/claim/* || "$claim" == https://*/claim/* ]] ||
    die "not a claim url: $claim"
  LOGIN_URL="${claim%%/claim/*}"
  url_is_private_or_tls "$LOGIN_URL" ||
    die "that claim link is http, which hands the token to the network: $LOGIN_URL"
  LOGIN_CODE="${claim##*/claim/}"
  # A link that has been through a chat client arrives with tracking on it.
  LOGIN_CODE="${LOGIN_CODE%%\?*}"
  [[ "$LOGIN_CODE" =~ ^gotgi_[A-Za-z0-9_-]{40,50}$ ]] || die "not a claim url: $claim"
}

# The claim spent: one POST, one token. The reply is the only time
# the plaintext exists outside the config file about to be written, so it is
# read here and removed here. Sets LOGIN_TOKEN and LOGIN_NAME.
_login_claim_exchange() {
  local reply http
  reply="$(mktemp)"
  # Expanded now on purpose: the path is gone by trap time. EXIT too, since
  # `die` exits without returning and would leave the plaintext in /tmp.
  # shellcheck disable=SC2064
  trap "rm -f '$reply'" RETURN EXIT
  # The code rides the body, not the URL: every log between here and the
  # service keeps the request line, and this is live until spent. printf is
  # a builtin, so unlike jq the code never reaches a world-readable cmdline.
  http="$(printf '{"code":"%s"}' "$LOGIN_CODE" |
    curl -sS -o "$reply" -w '%{http_code}' --connect-timeout 10 --max-time 30 \
      -X POST --data-binary @- "$LOGIN_URL/claim")" || die "could not reach $LOGIN_URL"
  if [[ "$http" != 200 ]]; then
    die "claim failed: $(printable "$(jq -r '.error // "the service answered '"$http"'"' "$reply" 2>/dev/null)")"
  fi
  LOGIN_TOKEN="$(jq -r '.token // empty' "$reply")"
  LOGIN_NAME="$(printable "$(jq -r '.name // empty' "$reply")")"
  [[ -n "$LOGIN_TOKEN" ]] || die "the claim reply carried no token"
}

# No claim: the server (given or asked for) and a token typed in.
# Sets LOGIN_URL and LOGIN_TOKEN.
_login_ask() {
  if [[ -n "$LOGIN_SERVER" ]]; then
    LOGIN_URL="$LOGIN_SERVER"
  else
    LOGIN_URL="$(prompt_line "GOTG service URL [https://gotg.dcraw.net]: " "https://gotg.dcraw.net")"
  fi
  LOGIN_URL="${LOGIN_URL%/}"
  [[ "$LOGIN_URL" == http://* || "$LOGIN_URL" == https://* ]] || die "service must be an http(s) URL: $LOGIN_URL"
  url_is_private_or_tls "$LOGIN_URL" ||
    die "that url is http, which sends the token in the clear: $LOGIN_URL"
  LOGIN_TOKEN="$(prompt_secret "Token: ")"
  [[ -n "$LOGIN_TOKEN" ]] || die "a token is required"
}

# The shape every bearer token has; anything else would also corrupt
# the curl config the token is spliced into. $1 the token.
_login_check_token() {
  [[ "$1" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || die "token contains characters no bearer token uses"
}

# The service must accept the token. Via curl --config on stdin,
# never argv: /proc/<pid>/cmdline is world-readable and this token does not
# expire. whoami is served by every pod; /catalog only by the library — and a
# claimed token deserves a check of the machinery that minted it.
# $1 url, $2 token, $3 the name a claim gave (empty for a typed token).
_login_probe() {
  local url="$1" token="$2" name="$3" probe="/catalog"
  [[ -z "$name" ]] || probe="/auth/whoami"
  printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl --config - -fsS --connect-timeout 10 --max-time 30 "$url$probe" >/dev/null 2>&1 ||
    die "the service at $url did not accept that token"
}

# Api.json: the url and token, and the name when a claim gave one,
# merged into whatever else the file holds, mode 600. $1 url, $2 token, $3 name.
_login_save() {
  local url="$1" token="$2" name="$3" file patch
  file="${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}"
  mkdir -p "$(dirname "$file")"
  patch="$(jq -n --arg url "$url" --arg token "$token" --arg name "$name" \
    '{url: $url, token: $token} + (if $name != "" then {name: $name} else {} end)')"
  json_merge_file "$file" "$patch" 600
  log "saved $file (mode 600)${name:+ — you are $name}"
}

# The File Browser era left a password behind; a dead credential in a
# 0600 file is still a credential.
_login_warn_old_password() {
  if [[ -f "$GOTG_CONFIG_FILE" ]] && jq -e '.password' "$GOTG_CONFIG_FILE" >/dev/null 2>&1; then
    warn "the old File Browser password in $GOTG_CONFIG_FILE is no longer used;"
    warn "remove it with: jq 'del(.username, .password, .server, .remote_root)' $GOTG_CONFIG_FILE"
  fi
}

cmd_login() {
  _login_parse_args "$@"
  if [[ -n "$LOGIN_CLAIM" ]]; then
    _login_claim_parts "$LOGIN_CLAIM"
    _login_claim_exchange
  else
    _login_ask
  fi
  _login_check_token "$LOGIN_TOKEN"
  _login_probe "$LOGIN_URL" "$LOGIN_TOKEN" "$LOGIN_NAME"
  _login_save "$LOGIN_URL" "$LOGIN_TOKEN" "$LOGIN_NAME"
  login_netrc "$LOGIN_URL" "$LOGIN_TOKEN"
  _login_warn_old_password
}
