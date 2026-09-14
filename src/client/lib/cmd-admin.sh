# shellcheck shell=bash
# Administration: invites minted, tokens listed and revoked, and the library
# scanned for what has arrived and what has gone.
#
# The admin token is deliberately not in api.json — it opens /admin and
# nothing else, and it belongs to whoever can already reach the cluster.

# Where the last scan's timestamp is remembered, so a bare `admin scan` means
# "since I last looked". State rather than config: losing it costs one noisy
# run, and it must never end up beside the credentials in api.json.
GOTG_ADMIN_MARK="${GOTG_ADMIN_MARK:-$GOTG_STATE_DIR/admin-scan.json}"

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
  import [--follow] [--timeout <seconds>]
                                run the importer now instead of waiting for
                                Sunday: a one-off Job cloned from the CronJob,
                                same image, same mounts. Needs kubectl and the
                                cluster, not the admin token. Prints what the
                                run flagged and its summary line
  art warm [--rate <n>] [--limit <n>] [--platform <p>] [--refresh]
                                resolve tile pictures for the whole catalog
                                and store them in the service, so no client
                                ever asks SteamGridDB again. Slow on purpose
                                (default 0.5 games/second) and resumable: a
                                second run only looks at what is still unknown.
                                Needs GOTG_INDEX_TOKEN
  art status                    how much of the catalog has a picture, a
                                recorded miss, or has never been looked at
  art search <title> [--assets N] [--limit N]
                                what SteamGridDB has under that name, through
                                the service's proxy — the first half of fixing
                                a tile that came out wrong
  art set <platform>/<id> <file|url>
                                this picture is the one, for everybody
  art miss <platform>/<id>      record that nobody has art for it
  art show <platform>/<id> [--out <file>]
                                what the cache holds for it, and which kind of
                                nothing when it holds none
  art forget <platform>/<id>    drop it, so the next warm looks again
  scan [--since <when>] [--all] [--json]
                                what the library has gained since you last ran
                                this (+), and which games the bytes have gone
                                from under (-). <when> is 7d, 12h, 2w or a full
                                2026-08-01T00:00:00Z; --all counts the whole
                                catalog as new. Neither moves the mark, so a
                                plain run still reports everything since the
                                last one.

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

# The shape of a scan timestamp, in one place: what admin_when accepts, what a
# remembered mark must look like before it is trusted back into a URL, and what
# the service must have answered with before it is written down as one.
GOTG_STAMP_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'

# Anything but an ISO stamp or a 7d/12h/2w offset is a typo, and a typo that
# reached the service as an empty --since would silently mean "the whole
# catalog is new".
admin_when() {
  local raw="$1" unit
  if [[ "$raw" =~ $GOTG_STAMP_RE ]]; then
    printf '%s' "$raw"
    return
  fi
  [[ "$raw" =~ ^([0-9]{1,4})([hdw])$ ]] ||
    die "--since takes 12h, 7d, 2w, or a stamp like 2026-08-01T00:00:00Z"
  case "${BASH_REMATCH[2]}" in
    h) unit=hours ;;
    d) unit=days ;;
    w) unit=weeks ;;
  esac
  date -u -d "${BASH_REMATCH[1]} $unit ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null ||
    die "could not work out when $raw was"
}

# Strip what a terminal would act on rather than print. @tsv escapes the tab
# and newline that could forge a row, but it passes ESC straight through, and
# the service's own isprintable() check is the *honest* server's — the endpoint
# this has to survive is a compromised one, which is the same reason `printable`
# exists in common.sh. Not `printable` here, though: `tr -cd '[:print:]'` under
# the C locale eats every multibyte character, and a Japanese dump title is a
# real title.
#
# Codepoints rather than a regex class, because jq's regex engine does not read
# \u escapes — "[\\u0000-\\u001f]" silently becomes [u0000-u001f] and eats
# letters and digits instead. C0, DEL, C1, and the bidi overrides that can
# reverse a name on screen.
GOTG_JQ_CLEAN='def clean: tostring | explode | map(select(
      . > 31 and . != 127
      and (. < 128 or . > 159)
      and (. < 8206 or . > 8207)
      and (. < 8234 or . > 8238)
      and (. < 8294 or . > 8297))) | implode;'

admin_scan_rows() {
  local reply="$1" only="${2:-}"
  if [[ "$only" != "missing-only" ]]; then
    jq -r "$GOTG_JQ_CLEAN"' .added[] | [(.platform|clean), (.id|clean), (.title|clean)] | @tsv' <<<"$reply" |
      while IFS=$'\t' read -r platform id title; do
        printf '%s+%s %-8s %-36s %s\n' "$C_OK" "$C_RESET" "$platform" "$id" "$title"
      done
  fi
  jq -r "$GOTG_JQ_CLEAN"' .missing[]
      | [(.platform|clean), (.id|clean), (.title|clean), (.missing_files|clean), (.files|clean)]
      | @tsv' <<<"$reply" |
    while IFS=$'\t' read -r platform id title gone of; do
      printf '%s-%s %-8s %-36s %s %s(%s of %s file(s) gone)%s\n' \
        "$C_ERROR" "$C_RESET" "$platform" "$id" "$title" "$C_MUTED" "$gone" "$of" "$C_RESET"
    done
}

admin_scan() {
  local since="" asked=0 as_json=0 all=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --since)
        [[ -n "${2:-}" ]] || die "--since needs a time"
        since="$(admin_when "$2")"
        asked=1
        shift 2
        ;;
      --all)
        all=1
        asked=1
        shift
        ;;
      --json)
        as_json=1
        shift
        ;;
      *) die "unknown option for scan: $1" ;;
    esac
  done
  # Both name a window, and --since would quietly win the assignment below.
  # Two answers to one question is a typo somebody is about to trust.
  ((all && ${#since})) && die "--all and --since name different windows; pass one"

  local mark=""
  if ((!asked)) && [[ -f "$GOTG_ADMIN_MARK" ]]; then
    mark="$(jq -r '.scanned_at // empty' "$GOTG_ADMIN_MARK" 2>/dev/null || true)"
    # Checked coming in as well as going out. A mark this command cannot have
    # written is a corrupt or hand-edited file, and passing it on unchecked
    # sends the service a --since nobody validated — which then fails on every
    # run afterwards, with nothing naming the file to delete.
    # The file is there, so a mark that will not read is a truncated write or
    # a hand edit — not the same thing as never having looked, and the run
    # that follows silently skips whatever arrived since the real mark.
    if [[ ! "$mark" =~ $GOTG_STAMP_RE ]]; then
      warn "the last-scan mark is unreadable, so this run cannot say what it missed: $GOTG_ADMIN_MARK"
      mark=""
    fi
  fi
  ((all)) || since="${since:-$mark}"

  local path="/admin/scan" reply
  [[ -n "$since" ]] && path+="?since=$since"
  reply="$(admin_call GET "$path")"

  # Without a mark and without a window, every game in the catalog is "new" —
  # true, and useless as a first impression of a library of thousands. So the
  # first run reports only what is missing and leaves the mark behind it.
  local first=0
  ((asked)) || [[ -n "$mark" ]] || first=1

  if ((as_json)); then
    printf '%s\n' "$reply"
  else
    local total added missing
    total="$(jq -r '.total' <<<"$reply")"
    added="$(jq -r '.added | length' <<<"$reply")"
    missing="$(jq -r '.missing | length' <<<"$reply")"
    if ((first)); then
      admin_scan_rows "$reply" missing-only
      log "first scan: $total game(s) in the catalog, $missing missing — run this again for what arrives next"
    else
      admin_scan_rows "$reply"
      log "$added added, $missing missing since ${since:-the beginning} — $total in the catalog"
    fi
    if [[ "$(jq -r '.suspect' <<<"$reply")" == "true" ]]; then
      warn "over a fifth of the catalog is missing — a library volume that failed to mount looks exactly like this"
    fi
    # A window that ends before it begins. Nothing but a clock that moved
    # backwards makes one, and anything imported in the stretch it replayed
    # sits below every window after it — so say so, because the summary line
    # above this reads exactly like a quiet library.
    if [[ "$(jq -r '.clock_stepped_back' <<<"$reply")" == "true" ]]; then
      warn "the service's clock has gone backwards since the last scan: games imported in between
     will not appear on their own. Re-check with: gotg admin scan --since <before the step>"
    fi
  fi

  # The mark moves only on a plain run. An explicit window is a question, and
  # one that moved the mark would eat the next run's news to answer it.
  if ((!asked)); then
    local stamp
    stamp="$(jq -r '.scanned_at // empty' <<<"$reply")"
    [[ "$stamp" =~ $GOTG_STAMP_RE ]] ||
      die "the service returned a scan time it cannot have made: $(printable "$stamp")"
    mkdir -p "$(dirname "$GOTG_ADMIN_MARK")"
    jq -n --arg t "$stamp" '{scanned_at: $t}' >"$GOTG_ADMIN_MARK"
  fi
}

# The importer runs weekly (0 4 * * 0), which is long enough that "what turned
# up?" has a stale answer most of the week. This runs it now: a one-off Job
# cloned from the CronJob, so the manual run is byte-for-byte the same pod spec
# the Sunday run gets — same image digest, same mounts, same env. The clone
# snapshots the template at creation, so a CronJob image update must land
# before this is invoked, never after.
#
# The one admin command on the cluster plane rather than the service's: there
# is deliberately no run-a-job endpoint, because the service holding cluster
# credentials would be a far bigger grant than the scan it fronts. Whoever can
# run an import can already reach the cluster — the same assumption the admin
# token's own setup line makes.
kubectl_bin() { printf '%s' "${GOTG_KUBECTL:-kubectl}"; }

admin_import() {
  local follow=0 timeout=1800
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --follow)
        follow=1
        shift
        ;;
      --timeout)
        [[ "${2:-}" =~ ^[1-9][0-9]*$ ]] || die "--timeout takes seconds"
        timeout="$2"
        shift 2
        ;;
      *) die "unknown option for import: $1" ;;
    esac
  done
  command -v "$(kubectl_bin)" >/dev/null 2>&1 || die "required command not found: kubectl"

  local cron="${GOTG_IMPORT_CRONJOB:-gotg-import}" job
  # Seconds in the name: Job names are unique per cluster and finished Jobs
  # linger, so two runs in one minute must not collide.
  job="gotg-import-manual-$(date -u +%Y%m%d-%H%M%S)"

  "$(kubectl_bin)" create job "$job" --from="cronjob/$cron" >/dev/null ||
    die "could not create a job from cronjob/$cron — is kubectl pointed at the cluster?"
  log "import running as job/$job"

  if ((follow)); then
    # Streams everything, thousands of noop lines included. The pod takes a
    # moment to exist, so logs is retried rather than raced.
    local tries=0
    until "$(kubectl_bin)" logs -f "job/$job" 2>/dev/null; do
      tries=$((tries + 1))
      ((tries < 60)) || break
      sleep 2
    done
  fi

  if ! "$(kubectl_bin)" wait --for=condition=complete "job/$job" --timeout="${timeout}s" >/dev/null 2>&1; then
    # Timed out, or the job failed. Which one decides the message.
    if [[ "$("$(kubectl_bin)" get "job/$job" -o jsonpath='{.status.failed}' 2>/dev/null)" =~ ^[1-9] ]]; then
      die "the import failed — read: kubectl logs job/$job"
    fi
    die "the import is still running after ${timeout}s — watch it: kubectl logs -f job/$job"
  fi

  # The lines a person acts on, not the thousands of noops: what was flagged,
  # then the one-line run summary. The full log stays a kubectl away. These
  # lines come off our own importer over kubectl — the cluster plane, not a
  # network endpoint — so they are not laundered through `printable`, which
  # would also eat the newlines between warnings.
  local logs
  logs="$("$(kubectl_bin)" logs "job/$job" 2>/dev/null)" || logs=""
  grep -E "WARNING|ERROR" <<<"$logs" | sed 's/^/  /' >&2 || true
  grep -E "^INFO (hardlink=|games-root pass)" <<<"$logs" | sed 's/^INFO //'
  log "done — gotg refresh picks up whatever arrived; gotg admin scan says what that was"
}

# --- the art cache ------------------------------------------------------------
#
# Warming is an operator's job rather than a client's, for the same reason
# catalog writes are: what lands in the cache is drawn by every other machine's
# grid, so it is written with the index token and nothing else.

admin_index_token() {
  [[ -n "${GOTG_INDEX_TOKEN:-}" ]] || die "GOTG_INDEX_TOKEN is not set.
     export GOTG_INDEX_TOKEN=\"\$(kubectl get secret gotg-api -o jsonpath='{.data.index-token}' | base64 -d)\""
}

# libretro lists a whole system's thumbnail names in one index, so a run that
# keeps them asks for six lists instead of six thousand. Under the state
# directory, beside the UI's own copy.
admin_art_names_cache() { printf '%s' "${GOTG_ART_NAMES_CACHE:-$GOTG_STATE_DIR/ui/libretro}"; }

admin_art_warm() {
  # --help before the credential check: asking what the flags are is not an
  # operation that needs one.
  local arg
  for arg in "$@"; do
    case "$arg" in
      -h | --help) python3 "${GOTG_ART_WARM:-$GOTG_ROOT/steam/warm.py}" --help; return 0 ;;
    esac
  done
  admin_index_token
  local url
  url="$(admin_url)"
  mkdir -p "$(admin_art_names_cache)"
  # The token reaches the warmer in its environment, never in argv.
  GOTG_INDEX_TOKEN="$GOTG_INDEX_TOKEN" python3 \
    "${GOTG_ART_WARM:-$GOTG_ROOT/steam/warm.py}" \
    --service "$url" --names-cache "$(admin_art_names_cache)" "$@"
}

# Reading the index is a client's right, not an admin's: the admin token opens
# /admin and nothing else, so this asks with the ordinary api.json credential.
admin_art_status() {
  service_have || die "no service configured — run: gotg login"
  local index
  index="$(service_curl -sS --max-time 60 "$(service_url)/art")" || die "could not reach the service"
  # An error body has no .art, and `null | length` is 0 — which would report an
  # empty cache for a service that has no cache at all, or would not say who we
  # are. Distinguished here rather than counted blindly.
  jq -e 'has("art")' <<<"$index" >/dev/null ||
    die "the service did not answer with an art index: $(jq -r '.error // .' <<<"$index")"
  jq -r '"art:    \(.art | length)\nmisses: \(.misses | length)"' <<<"$index"
}

# Curating: the cache is the source of truth, so a wrong picture and a wrong
# miss are both wrong for the whole fleet, and both are fixed here.
admin_art_curate() {
  admin_index_token
  GOTG_INDEX_TOKEN="$GOTG_INDEX_TOKEN" python3 \
    "${GOTG_ART_CURATE:-$GOTG_ROOT/steam/curate.py}" --service "$(admin_url)" "$@"
}

admin_art() {
  local verb="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  case "$verb" in
    warm) admin_art_warm "$@" ;;
    status) admin_art_status "$@" ;;
    search | set | miss | show | forget) admin_art_curate "$verb" "$@" ;;
    *)
      printf 'error: unknown art command: %s\n\n' "$verb" >&2
      admin_usage >&2
      exit 1
      ;;
  esac
}

cmd_admin() {
  local sub="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$sub" in
    invite) admin_invite "$@" ;;
    tokens | list | ls) admin_tokens "$@" ;;
    revoke) admin_revoke "$@" ;;
    import) admin_import "$@" ;;
    scan) admin_scan "$@" ;;
    art) admin_art "$@" ;;
    help | --help | -h | "") admin_usage ;;
    *)
      printf 'error: unknown admin command: %s\n\n' "$sub" >&2
      admin_usage >&2
      exit 1
      ;;
  esac
}
