# shellcheck shell=bash
# Token administration: invites minted, tokens listed and revoked, by name.
#
# The admin token is deliberately not in api.json — it opens /admin and
# nothing else, and it belongs to whoever can already reach the cluster.

admin_usage() {
  cat <<'EOF'
usage: gotg admin <command> [args]

  invite <name> [--ttl <days>] [--user <user>]
                                mint a single-use claim link for a person and
                                device — "alice-deck" — valid 7 days. The user
                                (default: the part before the first hyphen) is
                                whose saves the token reads and writes; every
                                device of one user shares them.
  tokens                        every token: name, display, last use
  revoke <name>                 end one token now, and cancel any invite
                                still outstanding for it; the person re-claims

The admin token comes from GOTG_ADMIN_TOKEN:
  export GOTG_ADMIN_TOKEN="$(kubectl get secret gotg-api -o jsonpath='{.data.admin-token}' | base64 -d)"
EOF
}

admin_url() {
  local file="${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}" url=""
  [[ -f "$file" ]] && url="$(jq -r '.url // empty' "$file" 2>/dev/null)"
  url="${url:-${GOTG_SERVICE_URL:-}}"
  [[ -n "$url" ]] || die "no service URL — run: gotg login (or set GOTG_SERVICE_URL)"
  printf '%s' "${url%/}"
}

admin_curl() {
  [[ -n "${GOTG_ADMIN_TOKEN:-}" ]] || die "GOTG_ADMIN_TOKEN is not set.
     export GOTG_ADMIN_TOKEN=\"\$(kubectl get secret gotg-api -o jsonpath='{.data.admin-token}' | base64 -d)\""
  # --config on stdin, never argv: /proc/<pid>/cmdline is world-readable.
  printf 'header = "Authorization: Bearer %s"\n' "$GOTG_ADMIN_TOKEN" |
    curl --config - -sS --connect-timeout 10 --max-time 30 "$@"
}

admin_call() {
  local method="$1" path="$2" body="${3:-}" url reply http
  url="$(admin_url)"
  reply="$(mktemp)"
  # Expanded now on purpose: the path is gone by trap time. EXIT too, since
  # `die` never returns and an invite reply is a live credential in /tmp.
  # shellcheck disable=SC2064
  trap "rm -f '$reply'" RETURN EXIT
  if [[ -n "$body" ]]; then
    http="$(admin_curl -X "$method" --data-binary "$body" -o "$reply" -w '%{http_code}' "$url$path")" ||
      die "could not reach $url"
  else
    http="$(admin_curl -X "$method" -o "$reply" -w '%{http_code}' "$url$path")" ||
      die "could not reach $url"
  fi
  if [[ "$http" != 200 ]]; then
    die "the service answered $http: $(printable "$(jq -r '.error // "no detail"' "$reply" 2>/dev/null)")"
  fi
  cat "$reply"
}

admin_invite() {
  local name="${1:-}" ttl=7 user=""
  [[ -n "$name" ]] || die "usage: gotg admin invite <name> [--ttl <days>] [--user <user>]"
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ttl)
        ttl="${2:-}"
        shift 2 || die "--ttl needs a number of days"
        ;;
      --user)
        user="${2:-}"
        shift 2 || die "--user needs a name"
        ;;
      *) die "unknown option for invite: $1" ;;
    esac
  done
  [[ "$ttl" =~ ^[1-9][0-9]{0,2}$ ]] || die "--ttl takes days, 1-999"

  local reply code
  reply="$(admin_call POST /admin/invites "$(jq -n --arg n "$name" --arg u "$user" --argjson t "$ttl"     '{name: $n, ttl_days: $t} + (if $u != "" then {user: $u} else {} end)')")"
  code="$(jq -r '.code' <<<"$reply")"
  log "one claim, $ttl day(s), then it is gone:"
  printf '%s/claim/%s\n' "$(admin_url)" "$code"
  log ""
  log "they run: gotg login --claim <that url>"
  log "a claim that fails as already-used means someone else got there — revoke and re-invite"
}

admin_tokens() {
  local reply
  reply="$(admin_call GET /admin/tokens)"
  # printf, not column: column escapes the display's ellipsis byte-by-byte
  # under the C locale.
  local fmt='%-20s %-12s %-12s %-21s %-21s %s\n'
  # shellcheck disable=SC2059
  printf "$fmt" "NAME" "USER" "TOKEN" "CREATED" "LAST USED" "REVOKED"
  jq -r '
    def when: if . == null then "-" else (. | todate) end;
    .tokens[] | [.name, (.user // "-"), .display, (.created_at | when), (.last_used_at | when), (.revoked_at | when)]
    | @tsv' <<<"$reply" |
    while IFS=$'\t' read -r name user display created used revoked; do
      # shellcheck disable=SC2059
      printf "$fmt" "$name" "$user" "$display" "$created" "$used" "$revoked"
    done
}

admin_revoke() {
  local name="${1:-}"
  [[ -n "$name" ]] || die "usage: gotg admin revoke <name>"
  admin_call DELETE "/admin/tokens/$name" >/dev/null
  log "revoked: $name takes effect everywhere within a minute"
}

cmd_admin() {
  local sub="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$sub" in
    invite) admin_invite "$@" ;;
    tokens | list | ls) admin_tokens "$@" ;;
    revoke) admin_revoke "$@" ;;
    help | --help | -h | "") admin_usage ;;
    *)
      printf 'error: unknown admin command: %s\n\n' "$sub" >&2
      admin_usage >&2
      exit 1
      ;;
  esac
}
