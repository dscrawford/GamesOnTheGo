# shellcheck shell=bash
# File Browser API.
#
# Tokens are short-lived, so every operation logs in fresh rather than caching
# one. That is a single extra request per command and removes a whole class of
# "it worked yesterday" failures.

API_TIMEOUT="${GOTG_API_TIMEOUT:-30}"

api_login() {
  local body http token
  body="$(jq -nc --arg u "$GOTG_USER" --arg p "$GOTG_PASS" \
    '{username: $u, password: $p, recaptcha: ""}')"

  # No --fail here: an HTTP error still carries the status we want to report on.
  # A non-zero exit from curl therefore means the request never completed, which
  # is a different problem from being turned away.
  local response
  response="$(curl -sS \
    --connect-timeout 10 --max-time "$API_TIMEOUT" \
    -H 'Content-Type: application/json' \
    -w '\n%{http_code}' \
    -X POST "$GOTG_SERVER/api/login" \
    --data "$body" 2>&1)" || {
    die "cannot reach $GOTG_SERVER: ${response:-connection failed}"
  }

  http="${response##*$'\n'}"
  token="${response%$'\n'*}"
  case "$http" in
    200) ;;
    401 | 403) die "login rejected by $GOTG_SERVER — check the username and password" ;;
    *) die "login failed (HTTP $http)" ;;
  esac
  [[ -n "$token" ]] || die "login returned an empty token"
  printf '%s' "$token"
}

# The token as a curl config, to be fed to curl on **stdin**.
#
# Two reasons it is not in the URL any more. /proc/<pid>/cmdline is
# world-readable, and a query parameter also survives in the server's access log
# and in any proxy in between — so `?auth=` hands the token to more readers than
# it needs. And File Browser removed `?auth=` in 2.45.0: this server is 2.32.0,
# so the change is preventive, but every new caller would otherwise be written
# against an API that is already gone upstream.
api_auth_config() { printf 'header = "X-Auth: %s"\n' "$1"; }

# curl with the token attached. Remaining arguments are curl's.
api_curl() {
  local token="$1"
  shift
  api_auth_config "$token" | curl --config - "$@"
}

# URL for a raw download. Directories are served as a zip built on the fly.
api_raw_url() {
  local remote_path="$1" type="${2:-file}"
  validate_remote_path "$remote_path"
  local encoded
  encoded="$(url_encode_path "$remote_path")"
  if [[ "$type" == "dir" ]]; then
    printf '%s/api/raw%s/?algo=zip' "$GOTG_SERVER" "$encoded"
  else
    printf '%s/api/raw%s' "$GOTG_SERVER" "$encoded"
  fi
}

# Fetch a small file (the catalog, a checksum sidecar) to stdout.
api_fetch() {
  local remote_path="$1" token="$2"
  api_curl "$token" -sS --fail --connect-timeout 10 --max-time "$API_TIMEOUT" \
    "$(api_raw_url "$remote_path")"
}
