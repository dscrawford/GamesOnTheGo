# shellcheck shell=bash
# The service kept current during a play, not only at the menu's Exit.

GOTG_SAVES_WATCH_POLL_MS="${GOTG_SAVES_WATCH_POLL_MS:-10000}"
GOTG_SAVES_WATCH_QUIET_MS="${GOTG_SAVES_WATCH_QUIET_MS:-15000}"
GOTG_SAVES_WATCH_EVERY_MS="${GOTG_SAVES_WATCH_EVERY_MS:-600000}"

# Digits or the default: $(( )) on an unchecked variable runs commands.
saves_watch_ms() {
  local name="$1" default="$2" want="${!1:-}"
  if [[ ! "$want" =~ ^[0-9]{1,9}$ ]] || ((10#$want < 1)); then
    warn "$name is not a number of milliseconds; using $default"
    want="$default"
  fi
  printf '%s' "$((10#$want))"
}

saves_ms_now() { printf '%s' "$(($(date +%s%N) / 1000000))"; }

saves_sleep_ms() { sleep "$(printf '%d.%03d' "$(($1 / 1000))" "$(($1 % 1000))")"; }

# The save files' names, sizes and times in one word.
saves_signature() {
  local attr="$1" members=()
  mapfile -t members < <(saves_members "$attr")
  if [[ ${#members[@]} -eq 0 ]]; then
    printf 'none'
    return 0
  fi
  (cd "$(env_state_dir "$attr")" && stat -c '%n %s %y' -- "${members[@]}" 2>/dev/null) | sha256sum | cut -d' ' -f1
}

# Field 22 of /proc/<pid>/stat, past the parenthesised name: a pid handed on
# to another process has another start time.
saves_pid_started() {
  local line rest
  read -r line <"/proc/$1/stat" 2>/dev/null || return 1
  rest="${line##*) }"
  # shellcheck disable=SC2086 # the fields are the point
  set -- $rest
  printf '%s' "${20}"
}

saves_pid_same() { [[ "$(saves_pid_started "$1" 2>/dev/null)" == "$2" ]]; }

# One machine, one environment, one sync at a time: a watcher's last push and
# the next launch's sync of the same environment must not interleave.
saves_lock_path() { printf '%s/locks/saves-%s.lock' "$GOTG_STATE_DIR" "$1"; }

# Push what is here, saying how it went by status rather than by dying:
#   0  pushed (SAVES_PUSH_GENERATION says which)
#   1  it could not be done; store_save said why
#   2  the service has moved on; SAVES_PUSH_HEAD is its head
#   3  nothing here to push
#   4  nothing changed since the last push
# shellcheck disable=SC2034
SAVES_PUSH_HEAD=""
SAVES_PUSH_GENERATION=""
saves_push_try() {
  local attr="$1" force="${2:-}" bundle hash meta rc=0
  SAVES_PUSH_HEAD="" SAVES_PUSH_GENERATION=""
  bundle="$(saves_tmp)/push-$attr.tar.zst"
  saves_bundle "$attr" "$bundle" || return 3
  hash="$(saves_hash "$bundle")"
  # Identical bytes hash the same, so a machine that has already pushed does
  # nothing — cheap enough to run after every session and during one.
  [[ "$hash" != "$(saves_journal_get "$attr" pushed_hash)" ]] || return 4

  # The parent hash rides along and the service advances the head atomically
  # or answers with what is there: no read-compare-write window.
  meta="$(store_save "$attr" "$bundle" "$(saves_journal_get "$attr" base_hash)" "$force")" || rc=$?
  if ((rc == 2)); then
    # shellcheck disable=SC2034
    SAVES_PUSH_HEAD="$meta"
    return 2
  elif ((rc != 0)); then
    return 1
  fi
  local at
  SAVES_PUSH_GENERATION="$(jq -r '.generation' <<<"$meta")"
  at="$(jq -r '.written_at // empty' <<<"$meta")"
  saves_journal_set "$attr" "$(jq -nc \
    --argjson generation "$SAVES_PUSH_GENERATION" --arg hash "$hash" --arg at "$at" \
    '{base_generation: $generation, base_hash: $hash, pushed_hash: $hash, base_written_at: $at}')"
}

saves_say_both_moved() {
  warn "$1: this machine and the remote both have new saves. Keeping
     what is here; reconcile with: gotg saves status"
}

# A push, said; its status is saves_push_try's.
saves_push_say() {
  local rc=0
  saves_push_try "$1" || rc=$?
  case "$rc" in
    0) log "$1: pushed generation $SAVES_PUSH_GENERATION" ;;
    1) warn "$1: could not push the saves; they are kept here for the next try" ;;
    2) saves_say_both_moved "$1" ;;
  esac
  return "$rc"
}

# One reconciliation of one environment with the service, never fatal:
#   here-newer, here-only      push
#   remote-newer, remote-only  pull, when `mode` is `both`
#   conflict                   said, and left for a person
#   same, offline              nothing
saves_sync_one() {
  with_lock "$(saves_lock_path "$1")" 30 "$1: another sync has its saves; not now" \
    saves_sync_one_locked "$@"
}

saves_sync_one_locked() {
  local attr="$1" mode="${2:-both}" check state rc=0
  check="$(saves_check_one "$attr")" || {
    warn "$attr: could not look at the saves here; not synced"
    return 0
  }
  state="$(jq -r '.state' <<<"$check")"
  case "$state" in
    here-newer | here-only)
      saves_push_say "$attr" || true
      ;;
    remote-newer | remote-only)
      [[ "$mode" == "both" ]] || return 0
      saves_pull_one "$attr"
      ;;
    conflict) saves_say_both_moved "$attr" ;;
  esac
  return 0
}

# `gotg saves sync [<id>|--all] [--push-only]`: where a launch, the picker and
# the watcher meet.
saves_cmd_sync() {
  config_load
  local want="" mode="both" arg
  for arg in "$@"; do
    case "$arg" in
      --push-only) mode="push" ;;
      *) want="$arg" ;;
    esac
  done
  saves_have_remote || {
    log "no service configured; nothing to sync with"
    return 0
  }
  local attrs=() attr
  mapfile -t attrs < <(saves_resolve "$want")
  for attr in "${attrs[@]}"; do
    # A subshell: a die stops this environment's sync, not the next one's.
    (saves_sync_one "$attr" "$mode") || true
  done
}

# On the way into a game. Divergence is left to `gotg saves status`; a launch
# is the wrong place to decide it.
saves_sync_auto() {
  local attr="$1"
  saves_have_remote || return 0
  [[ -f "$(env_saves_manifest "$attr")" ]] || return 0
  saves_sync_one "$attr" both
}

# `gotg saves watch <env> --follow <pid>`.
saves_cmd_watch() {
  local attr="" pid="" arg
  while [[ $# -gt 0 ]]; do
    arg="$1"
    shift
    case "$arg" in
      --follow) pid="${1:-}" && shift ;;
      *) attr="$arg" ;;
    esac
  done
  [[ -n "$attr" && "$pid" =~ ^[1-9][0-9]*$ ]] || die "usage: gotg saves watch <env> --follow <pid>"
  validate_attr "$attr"
  config_load
  [[ -f "$(env_saves_manifest "$attr")" ]] || return 0
  saves_have_remote || return 0

  local poll quiet every born
  poll="$(saves_watch_ms GOTG_SAVES_WATCH_POLL_MS 10000)"
  quiet="$(saves_watch_ms GOTG_SAVES_WATCH_QUIET_MS 15000)"
  every="$(saves_watch_ms GOTG_SAVES_WATCH_EVERY_MS 600000)"
  born="$(saves_pid_started "$pid")" || born=""

  # Starting counts as a change: a save written between the launch and this
  # first look would otherwise wait for the clock.
  local last changed_at sig now next_check
  last="$(saves_signature "$attr")"
  changed_at="$(saves_ms_now)"
  next_check=$((changed_at + every))
  while [[ -n "$born" ]] && saves_pid_same "$pid" "$born"; do
    saves_sleep_ms "$poll"
    saves_pid_same "$pid" "$born" || break
    now="$(saves_ms_now)"
    sig="$(saves_signature "$attr")"
    if [[ "$sig" != "$last" ]]; then
      # Something was written; wait for it to finish. A half-written save
      # pushed whole would be a generation nobody can load.
      last="$sig"
      changed_at="$now"
      continue
    fi
    if [[ -n "$changed_at" ]] && ((now - changed_at >= quiet)); then
      changed_at=""
      saves_watch_tick "$attr" "$sig"
      next_check=$((now + every))
    elif ((now >= next_check)); then
      saves_watch_tick "$attr" "$sig"
      next_check=$((now + every))
    fi
  done
  saves_watch_tick "$attr" "$(saves_signature "$attr")"
}

# A push, unless the files are as they were at the last push that went
# through -- so a push that failed is tried again on the next tick, and
# divergence is said once per change rather than every ten minutes. Push
# only: the files under a running game are the emulator's to write.
SAVES_WATCH_PUSHED_SIG=""
saves_watch_tick() {
  local attr="$1" sig="$2" rc=0
  [[ "$sig" != "$SAVES_WATCH_PUSHED_SIG" ]] || return 0
  # A subshell: the lock's die on a timeout is a failed push, not the end of
  # the watcher.
  (with_lock "$(saves_lock_path "$attr")" 30 "$attr: another sync has its saves; later" \
    saves_push_say "$attr") || rc=$?
  ((rc == 1)) || SAVES_WATCH_PUSHED_SIG="$sig"
}

# Detached: setsid takes it out of the game's process group, so Steam
# stopping the game does not stop the last push.
saves_watch_start() {
  local pid="$1" attr="$2"
  [[ "${GOTG_SAVES_WATCH:-1}" != "0" ]] || return 0
  [[ -f "$(env_saves_manifest "$attr")" ]] || return 0
  saves_have_remote || return 0
  command -v setsid >/dev/null 2>&1 || return 1
  setsid "$GOTG_ROOT/bin/gotg" saves watch "$attr" --follow "$pid" </dev/null 3>&- &
  disown
}
