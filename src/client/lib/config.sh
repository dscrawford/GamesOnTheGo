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

  local tmp="$GOTG_CONFIG_FILE.tmp"
  # Create with the right mode before writing, so it is never briefly
  # world-readable.
  : >"$tmp"
  chmod 600 "$tmp"
  jq -n --argjson existing "$existing" --argjson patch "$patch" '$existing + $patch' >"$tmp"
  mv "$tmp" "$GOTG_CONFIG_FILE"
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
  local url="$1" token="$2" host netrc tmp conf system current
  host="${url#*://}"
  host="${host%%/*}"
  host="${host%%:*}"
  [[ "$host" =~ ^[A-Za-z0-9.-]+$ ]] || {
    warn "not writing a netrc for $url: no host Nix could match"
    return 0
  }
  netrc="$GOTG_CONFIG_DIR/netrc"
  tmp="$(mktemp "$netrc.XXXXXX")"
  chmod 600 "$tmp"
  {
    [[ ! -f "$netrc" ]] || grep -v "^machine $host " "$netrc" || true
    printf 'machine %s login gotg password %s\n' "$host" "$token"
  } >"$tmp"
  mv "$tmp" "$netrc"

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

cmd_login() {
  local url token name="" server=""
  # `nix run <library>#login`: the library knows its server, so the one
  # question left is the token.
  if [[ "${1:-}" == "--server" ]]; then
    server="${2:-}"
    [[ -n "$server" ]] || die "usage: gotg login [--claim <url>] [--server <url>]"
    shift 2
  fi
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
    if [[ -n "$server" ]]; then
      url="$server"
    else
      url="$(prompt_line "GOTG service URL [https://gotg.dcraw.net]: " "https://gotg.dcraw.net")"
    fi
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

  login_netrc "$url" "$token"

  # The File Browser era left a password behind; a dead credential in a 0600
  # file is still a credential.
  if [[ -f "$GOTG_CONFIG_FILE" ]] && jq -e '.password' "$GOTG_CONFIG_FILE" >/dev/null 2>&1; then
    warn "the old File Browser password in $GOTG_CONFIG_FILE is no longer used;"
    warn "remove it with: jq 'del(.username, .password, .server, .remote_root)' $GOTG_CONFIG_FILE"
  fi
}
