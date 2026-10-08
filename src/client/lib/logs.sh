# shellcheck shell=bash
# Opt-in session logs: with `share_logs` on, every game session's output is
# kept and sent to the service when the game ends, so a bug seen on a Deck can
# be read from the desk. Nothing is captured or sent while it is off.
#
# `gotg play` execs into the game and cannot wait for it, so the capture is a
# tee that survives the exec and a detached watcher that does the rest.

GOTG_LOGS_KEEP_PENDING=10
GOTG_LOGS_SESSION_RE='^[0-9]{8}T[0-9]{6}Z-env-[a-z0-9_-]+$'
GOTG_LOGS_BUNDLE_MAX=33554432

logs_root() { printf '%s/sessions' "$GOTG_STATE_DIR"; }

logs_sharing() {
  config_exists && [[ "$(config_get share_logs 2>/dev/null)" == "true" ]]
}

# Digits or the default: the value reaches arithmetic and head -c.
logs_console_cap() {
  local want="${GOTG_LOGS_CONSOLE_CAP:-16777216}"
  [[ "$want" =~ ^[0-9]{1,12}$ ]] || want=16777216
  printf '%s' "$want"
}

logs_usage() {
  cat <<'USAGE'
usage: gotg logs <on|off|status>
       gotg logs push <session-dir> | --pending

  on / off   keep every game session's output and send it to the server when
             the game ends (off by default; nothing is sent while off)
  status     prints on or off
  push       send one kept session now, or retry every kept one (--pending)
USAGE
}

cmd_logs() {
  local sub="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$sub" in
    on)
      config_patch '{"share_logs": true}'
      log "session logs are on: each game's output is sent to the server when it ends"
      ;;
    off)
      config_patch '{"share_logs": false}'
      log "session logs are off"
      ;;
    status) if logs_sharing; then echo on; else echo off; fi ;;
    push)
      if [[ "${1:-}" == "--pending" ]]; then
        logs_push_pending
      else
        [[ -n "${1:-}" ]] || die "usage: gotg logs push <session-dir> | --pending"
        logs_push "$1"
      fi
      ;;
    # Not in the usage: the detached watcher `gotg play` starts.
    watch) logs_watch "${1:-}" "${2:-}" ;;
    help | --help | -h) logs_usage ;;
    *)
      logs_usage >&2
      return 1
      ;;
  esac
}

# --------------------------------------------------------------------- capture

# The same two-line DMI read as env/machine.sh, which is embedded in the
# environments' launchers and not reachable from here.
logs_machine() {
  local product
  if [[ -n "${GOTG_MACHINE:-}" ]]; then
    printf '%s' "$GOTG_MACHINE"
    return 0
  fi
  product="$(cat "${GOTG_DMI_PRODUCT_FILE:-/sys/devices/virtual/dmi/id/product_name}" 2>/dev/null || true)"
  case "$product" in
    Jupiter | Galileo) printf 'deck' ;;
    *) printf 'desktop' ;;
  esac
}

logs_gotg_rev() {
  local library rev=""
  library="$(gotg_library 2>/dev/null)" || library=""
  [[ -z "$library" ]] || rev="$(updates_gotg_locked "$library")"
  printf '%s' "${rev:-checkout}"
}

# Make this session's directory and send this shell's stdout and stderr through
# a tee into its console.log, capped. A drainer behind a fifo keeps reading
# after the cap, so a long game never gets EPIPE from a full log.
# Leaves LOGS_SESSION_DIR set. Called in the shell that will exec the game.
logs_session_begin() {
  local attr="$1" game="$2" variant="${3:-}" stamp iso id dir fifo cap
  read -r stamp iso < <(date -u +'%Y%m%dT%H%M%SZ %Y-%m-%dT%H:%M:%SZ')
  id="$stamp-$attr"
  [[ "$id" =~ $GOTG_LOGS_SESSION_RE ]] || return 1
  dir="$(logs_root)/$id"
  mkdir -p "$(logs_root)" && mkdir -m 700 "$dir" || return 1
  jq -n --arg attr "$attr" --arg platform "$(manifest_field "$game" platform)" \
    --arg id "$(manifest_field "$game" id)" --arg variant "$variant" \
    --arg machine "$(logs_machine)" --arg rev "$(logs_gotg_rev)" --arg started "$iso" \
    '{version: 1, attr: $attr, platform: $platform, id: $id, variant: $variant,
      machine: $machine, gotg: $rev, started: $started, console_truncated: false}' \
    >"$dir/session.json" || return 1
  printf '%s' "$$" >"$dir/pid"
  logs_prune_pending

  cap="$(logs_console_cap)"
  fifo="$dir/.console.fifo"
  mkfifo -m 600 "$fifo" || return 1
  # One byte past the cap, so a console of exactly the cap is not called cut.
  { head -c $((cap + 1)); cat >/dev/null; : >"$dir/.console.done"; } <"$fifo" >"$dir/console.log" 3>&- &
  exec 2> >(tee -- "$fifo" >&2 3>&-) > >(tee -- "$fifo" 3>&-)
  # shellcheck disable=SC2034 # read by play_launch
  LOGS_SESSION_DIR="$dir"
}

# Names lead with their start time, so sorted order is age order.
logs_prune_pending() {
  local dirs=() n
  mapfile -t dirs < <(find "$(logs_root)" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
  n=$((${#dirs[@]} - GOTG_LOGS_KEEP_PENDING))
  ((n > 0)) || return 0
  rm -rf -- "${dirs[@]:0:n}"
}

# Detached: setsid takes it out of the game's process group, so the kill
# switch stopping the game does not stop the upload.
logs_watch_start() {
  local dir="$1" pid="$2"
  command -v setsid >/dev/null 2>&1 || return 1
  setsid "$GOTG_ROOT/bin/gotg" logs watch "$dir" "$pid" </dev/null >/dev/null 2>&1 3>&- &
  disown
}

logs_watch() {
  local dir="$1" pid="$2" i
  [[ -f "$dir/session.json" && "$pid" =~ ^[0-9]+$ ]] || return 1
  exec 2>>"$dir/watch.log"
  printf '%s' "$$" >"$dir/watcher"
  logs_push_pending || true
  while kill -0 "$pid" 2>/dev/null; do sleep 1; done
  # The tee may still be flushing the game's last words.
  for ((i = 0; i < 50; i++)); do
    [[ -e "$dir/.console.done" ]] && break
    sleep 0.2
  done
  logs_end "$dir"
  logs_sharing || return 0
  logs_push "$dir" || true
}

# danstick keeps one log for the daemon; this session's end is when it is
# copied, so it is what the daemon said up to now.
logs_end() {
  local dir="$1" danstick="${XDG_RUNTIME_DIR:-}/danstick/danstick.log" tmp
  [[ -z "${XDG_RUNTIME_DIR:-}" || ! -f "$danstick" ]] || tail -c 8388608 "$danstick" >"$dir/danstick.log" 2>/dev/null || true
  tmp="$dir/session.json.tmp"
  jq --arg now "$(iso_now)" '.ended = (.ended // $now)' "$dir/session.json" >"$tmp" &&
    mv -f "$tmp" "$dir/session.json"
}

# ------------------------------------------------------------------- redaction

# Credentials out before anything leaves the machine: three spellings of a
# token, then any long run that is all hex or mixes case and digits. Runs stop
# at '/' and '.', and a lowercase-only run is left alone, so store paths and
# attribute names stay readable.
logs_redact() {
  LC_ALL=C sed -E \
    -e 's/(bearer[[:space:]]+)[^[:space:]"'"'"',;]+/\1<redacted>/Ig' \
    -e 's/(token=)[^&[:space:]"'"'"']+/\1<redacted>/Ig' \
    -e 's/("token"[[:space:]]*:[[:space:]]*")[^"]*"/\1<redacted>"/Ig' |
    LC_ALL=C awk '
      function cred(r) {
        if (length(r) < 32) return 0
        if (r ~ /^[0-9A-Fa-f]+$/) return 1
        return (r ~ /[A-Z]/ && r ~ /[a-z]/ && r ~ /[0-9]/)
      }
      {
        line = $0; out = ""
        while (match(line, /[A-Za-z0-9+_-]+/)) {
          run = substr(line, RSTART, RLENGTH)
          out = out substr(line, 1, RSTART - 1) (cred(run) ? "<redacted>" : run)
          line = substr(line, RSTART + RLENGTH)
        }
        print out line
      }'
}

# ---------------------------------------------------------------------- bundle

# <dir> -> <out>: a zstd tar of a redacted copy, so the kept directory is
# never altered and a retry redacts the same way. Returns 2 when the result
# is over what the service takes, which no retry will fix.
logs_bundle() {
  local dir="${1%/}" out="$2" stage cap size truncated=false
  stage="$(mktemp -d "${TMPDIR:-/tmp}/gotg-logs.XXXXXX")" || return 1
  cap="$(logs_console_cap)"
  size="$(stat -c '%s' "$dir/console.log" 2>/dev/null || echo 0)"
  ((size <= cap)) || truncated=true
  head -c "$cap" "$dir/console.log" 2>/dev/null | logs_redact >"$stage/console.log"
  [[ ! -f "$dir/danstick.log" ]] || tail -c 8388608 "$dir/danstick.log" | logs_redact >"$stage/danstick.log"
  jq --argjson t "$truncated" --arg now "$(iso_now)" \
    '.console_truncated = $t | .ended = (.ended // $now)' "$dir/session.json" >"$stage/session.json" || {
    rm -rf "$stage"
    return 1
  }
  local members=(session.json console.log)
  [[ ! -f "$stage/danstick.log" ]] || members+=(danstick.log)
  tar --zstd -C "$stage" -cf "$out" -- "${members[@]}" || {
    rm -rf "$stage"
    return 1
  }
  rm -rf "$stage"
  size="$(stat -c '%s' "$out")"
  ((size <= GOTG_LOGS_BUNDLE_MAX)) || return 2
}

# --------------------------------------------------------------------- upload

# One try. 0: kept by the service and removed here. 1: this directory can
# never be sent, and is removed. 2: the service could not take it now; kept.
logs_push() {
  local dir="${1%/}" id attr tmp out http rc=0
  [[ -f "$dir/session.json" ]] || {
    warn "$dir is not a session directory"
    return 1
  }
  id="${dir##*/}"
  attr="$(jq -r '.attr // empty' "$dir/session.json")"
  [[ "$id" =~ $GOTG_LOGS_SESSION_RE && "$attr" =~ ^env-[a-z0-9_-]+$ ]] || {
    warn "$dir has no session id the service would take"
    return 1
  }
  saves_have_remote || {
    warn "not logged in; the session at $dir is kept"
    return 2
  }
  tmp="$(mktemp "${TMPDIR:-/tmp}/gotg-logs-bundle.XXXXXX")"
  out="$tmp.reply"
  logs_bundle "$dir" "$tmp" || rc=$?
  if ((rc != 0)); then
    rm -f "$tmp" "$out"
    ((rc == 2)) && rm -rf -- "$dir"
    warn "could not bundle $dir"
    return 1
  fi
  http="$(saves_api_curl -X PUT --data-binary "@$tmp" -H "Content-Type: application/zstd" \
    -H "X-Gotg-Attr: $attr" -H "X-Gotg-Device: $(device_name)" \
    -o "$out" -w '%{http_code}' "$(saves_api_url)/logs/$id")" || http=000
  rm -f "$tmp"
  case "$http" in
    200)
      rm -f "$out"
      rm -rf -- "$dir"
      ;;
    400 | 413)
      warn "the service refused session $id ($http); dropping it"
      rm -f "$out"
      rm -rf -- "$dir"
      return 1
      ;;
    *)
      warn "the service answered $http for session $id; kept at $dir"
      rm -f "$out"
      return 2
      ;;
  esac
}

# Every kept session whose game has ended. A directory with a live watcher is
# being finished by it; with no end time and a live pid, a game still running;
# with a dead one, its watcher was lost. Stops at the first unreachable service rather than waiting on each.
logs_push_pending() {
  local dir pid rc
  logs_sharing || return 0
  for dir in "$(logs_root)"/*/; do
    dir="${dir%/}"
    [[ -f "$dir/session.json" ]] || continue
    pid="$(cat "$dir/watcher" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then continue; fi
    if ! jq -e '.ended' "$dir/session.json" >/dev/null 2>&1; then
      pid="$(cat "$dir/pid" 2>/dev/null || true)"
      if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then continue; fi
    fi
    rc=0
    logs_push "$dir" || rc=$?
    ((rc != 2)) || return 2
  done
  return 0
}
