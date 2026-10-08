# shellcheck shell=bash
# Shared helpers: logging, validation, and the paths everything else agrees on.
#
# These are read by the other lib/*.sh files, which shellcheck analyses one file
# at a time and so cannot see.
# shellcheck disable=SC2034

# Where runtime state lives. Kept out of the nix store so it survives rebuilds.
GOTG_CONFIG_DIR="${GOTG_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/gotg}"
GOTG_STATE_DIR="${GOTG_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/gotg}"
GOTG_CONFIG_FILE="$GOTG_CONFIG_DIR/config.json"
GOTG_CACHE_FILE="$GOTG_STATE_DIR/manifest.json"
GOTG_ROOTS_DIR="$GOTG_STATE_DIR/roots"
GOTG_LOG_DIR="$GOTG_STATE_DIR/logs"
GOTG_APP_ROOT="$GOTG_STATE_DIR/app"
# The picker, the same way: a symlink sync keeps current, so what Steam runs
# is what was built last rather than whatever was on PATH when Steam started.
# Not "ui": that directory is already the picker's own state -- artwork and
# the like -- and a symlink cannot be put where a directory is.
GOTG_UI_ROOT="$GOTG_STATE_DIR/picker"
GOTG_SAVES_DIR="$GOTG_STATE_DIR/saves"

# Where games land. One directory per platform, mirroring the server layout.
# This is the default of possibly several — see storage.sh, which reads the
# rest from the config once it is loaded. Set in the environment, it is the
# only one.
GOTG_GAMES_DIR_EXPLICIT="${GOTG_GAMES_DIR:-}"
GOTG_PARTIAL_DIR_EXPLICIT="${GOTG_PARTIAL_DIR:-}"
GOTG_GAMES_DIR="${GOTG_GAMES_DIR:-$HOME/Games}"
GOTG_PARTIAL_DIR="${GOTG_PARTIAL_DIR:-$GOTG_GAMES_DIR/.gotg-partial}"

# The entry-id contract, shared with the importer.
GOTG_ID_RE='^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$'
# Platform slugs name a directory under ~/Games, so they are checked too.
GOTG_PLATFORM_RE='^[a-z0-9][a-z0-9_-]{0,15}$'
# Environment attributes are flake attributes, GC root names, local state
# directories and remote directories all at once, so they are checked wherever
# one arrives from somewhere other than env_attr.
GOTG_ATTR_RE='^env-[a-z0-9][a-z0-9_-]*$'

# Plain by default: colour marks the exceptions, and everything being an
# exception is the same as nothing being one. The label is coloured rather than
# the message, so the text stays readable when it is quoted or grepped.
log() { printf '%s\n' "$*" >&2; }
# For strings a server chose: a hostile endpoint must not write live escape
# sequences into the terminal through an error message or a name.
printable() { tr -cd '[:print:]' <<<"$*"; }
warn() { printf '%swarning:%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; }
success() { printf '%s%s%s\n' "$C_OK" "$*" "$C_RESET" >&2; }

die() {
  printf '%serror:%s %s\n' "$C_ERROR" "$C_RESET" "$*" >&2
  exit 1
}

# True when there is a person watching a terminal, false under Steam or cron.
is_tty() { [[ -t 1 && -t 2 ]]; }

# True when a graphical progress dialog can be shown — and wanted.
# GOTG_NO_DIALOG is for a caller that owns the screen already: the UI streams
# build output into its own loader view, and a zenity raised beside it would at
# best double-report and at worst never composite (gamescope in game mode shows
# only the game window). One choke point, so no call site can forget.
has_display() {
  [[ -z "${GOTG_NO_DIALOG:-}" ]] || return 1
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]
}

# The private directory a progress dialog's fifo lives in, and its removal.
#
# Both of these exist so that `rm -rf` never sees a name that came from
# anywhere but mktemp: the directory is created here, holds one fifo, and is
# removed only if it is still a directory under the temp root. A path that is
# empty, unset (set -u would have stopped that anyway) or somewhere else is
# refused rather than removed.
dialog_dir() { mktemp -d "${TMPDIR:-/tmp}/gotg-dialog.XXXXXXXX"; }
dialog_dir_remove() {
  local dir="$1"
  [[ -n "$dir" && -d "$dir" && "$dir" == "${TMPDIR:-/tmp}/gotg-dialog."* ]] || return 0
  rm -rf -- "$dir"
}

# The dialog program, named so a test can stand one in: the packaged client
# puts nixpkgs' zenity first on PATH, where a stub could never win.
zenity_bin() { printf '%s' "${GOTG_ZENITY:-zenity}"; }
have_zenity() { command -v "$(zenity_bin)" >/dev/null 2>&1; }

# zenity, in a locale that can read its own arguments. Under Steam, over ssh
# and in a few other places the locale is C, and GLib's option parser then
# refuses any non-ASCII argument -- an ellipsis, a game title with an accent --
# with "This option is not available". On the Deck that took every environment
# build down as "the progress dialog closed".
zenity_run() {
  local rc=0
  LC_ALL=C.UTF-8 "$(zenity_bin)" "$@" || rc=$?
  # 0 is done, 1 is the person cancelling, 5 is its own timeout. Anything
  # else is zenity failing, and the log should say in what surroundings.
  case "$rc" in
    0 | 1 | 5) ;;
    *) warn "zenity exited $rc (LANG=${LANG:-} LC_ALL=C.UTF-8 DISPLAY=${DISPLAY:-} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} zenity=$(command -v "$(zenity_bin)" 2>/dev/null))" ;;
  esac
  return "$rc"
}

# Whether a dialog started at all: zenity that dies within its first moments
# never showed anything, and the work it was fronting should go on without it.
DIALOG_START_GRACE="${GOTG_DIALOG_START_GRACE:-0.3}"
dialog_started() {
  sleep "$DIALOG_START_GRACE"
  kill -0 "$1" 2>/dev/null
}

# Run a command behind a zenity progress dialog and supervise both:
#
#   zenity_supervise <what> <stopped> [<tick...>] -- <zenity args...> -- <work...> -- <fallback...>
#
# <what> is the gerund the messages use ("building env-n64"), <stopped> the
# first words of the death when the person closes the dialog ("build stopped").
# The work runs in the background and its status is returned. A tick, when
# there is one, is run each interval as `tick... <fd> tick`, to write to the
# dialog's pipe on that descriptor, and once as `tick... <fd> failed` when the
# work failed (instead of the bar being filled). The fallback is the work again
# without a dialog, for a dialog that never came up.
#
# The rules, and the day each was learned:
# - A dialog that never came up (dead inside the grace) is not a cancellation:
#   the work goes on without it. On the Deck zenity refused GOTG's own arguments
#   and every build was killed as "cancelled".
# - A dialog that goes away mid-work says why in its exit status: 1 is the
#   person closing it, and that stops the work (the partial stays, so the next
#   run resumes); anything else -- an option it refused, a display it lost --
#   is the dialog's problem and not a reason to lose a build or a download.
# - 100 is written only when the work succeeded: a bar jumping to full on a
#   failure tells the person the opposite of what happened.
#
# _download_zenity and _env_build_zenity each carried this loop, differing in
# the tick, two messages and the fd (9 and 6, one of them the lock's); the
# download's cancel path had no test at all.
zenity_supervise() {
  # The dialog is the fifo's only reader, and it can go away between any
  # check and the next write -- for certain after the work has ended, which is
  # where the full bar is written. A write to a fifo nobody reads is SIGPIPE,
  # and SIGPIPE ends this shell with no word said: a download that finished
  # after its dialog had been closed died with status 141 and no message.
  # Ignored for the supervisor's lifetime, the write fails with EPIPE instead,
  # which `|| true` can absorb. The work inherits the disposition; curl writes
  # to a file and nix to a socket it owns, neither minds.
  # Restored on the way out by a RETURN trap rather than `|| rc=$?`: the
  # latter would have run the body with errexit off, and a mkfifo that
  # failed used to end the client rather than carry on without a dialog.
  trap '' PIPE
  trap 'trap - PIPE; trap - RETURN' RETURN
  _zenity_supervise "$@"
}

# One line to the dialog's fifo. Its reader may be gone (see above), so the
# caller says what a failed write means -- for every caller so far, nothing.
dialog_say() { printf '%s\n' "$2" >&"$1"; }

_zenity_supervise() {
  local what="$1" stopped="$2" part=tick fd
  shift 2
  local tick=() zargs=() work=() fallback=() arg
  for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
      case "$part" in
        tick) part=zargs ;;
        zargs) part=work ;;
        work) part=fallback ;;
      esac
      continue
    fi
    case "$part" in
      tick) tick+=("$arg") ;;
      zargs) zargs+=("$arg") ;;
      work) work+=("$arg") ;;
      fallback) fallback+=("$arg") ;;
    esac
  done

  # A private directory for the fifo: mktemp -u then mkfifo races with anything
  # else that could claim the name in between.
  local pipedir pipe work_pid zen_pid status=0 zrc
  pipedir="$(dialog_dir)"
  pipe="$pipedir/progress"
  mkfifo "$pipe"

  zenity_run "${zargs[@]}" <"$pipe" &
  zen_pid=$!
  exec {fd}>"$pipe"

  if ! dialog_started "$zen_pid"; then
    exec {fd}>&-
    dialog_dir_remove "$pipedir"
    warn "the progress dialog could not start; $what without it"
    "${fallback[@]}"
    return
  fi

  "${work[@]}" &
  work_pid=$!

  while kill -0 "$work_pid" 2>/dev/null; do
    if ! kill -0 "$zen_pid" 2>/dev/null; then
      # `wait` is a command like any other to errexit: `wait; zrc=$?` left
      # the shell before zrc was read whenever the dialog's exit was not 0,
      # which for a cancel (1) is always. The build path never saw it because
      # its caller ran it under `||`.
      wait "$zen_pid" 2>/dev/null && zrc=0 || zrc=$?
      exec {fd}>&-
      dialog_dir_remove "$pipedir"
      if [[ "$zrc" -eq 1 ]]; then
        kill "$work_pid" 2>/dev/null || true
        wait "$work_pid" 2>/dev/null || true
        die "$stopped: the progress dialog was cancelled"
      fi
      warn "the progress dialog went away (zenity exited $zrc); $what without it"
      wait "$work_pid" || status=$?
      return "$status"
    fi
    if ((${#tick[@]} > 0)); then "${tick[@]}" "$fd" tick; fi
    sleep "${GOTG_PROGRESS_TICK:-0.5}"
  done

  wait "$work_pid" || status=$?
  if [[ "$status" -eq 0 ]]; then
    dialog_say "$fd" 100 2>/dev/null || true
  elif ((${#tick[@]} > 0)); then
    "${tick[@]}" "$fd" failed || true
  fi
  exec {fd}>&-
  dialog_dir_remove "$pipedir"
  wait "$zen_pid" 2>/dev/null || true
  return "$status"
}

# Ids come from the server and end up as paths and filenames, so check them
# rather than trusting the catalog.
validate_id() {
  local id="$1"
  [[ -n "$id" ]] || die "empty game id"
  [[ "$id" =~ $GOTG_ID_RE ]] || die "invalid game id: $id"
}

# The platform becomes a directory name under ~/Games, and the paths built from
# it are passed to `rm -rf` and used as redirection targets. An unchecked value
# like "../.." would put those outside the games directory entirely.
validate_platform() {
  local platform="$1"
  [[ -n "$platform" ]] || die "catalog entry has no platform"
  [[ "$platform" =~ $GOTG_PLATFORM_RE ]] || die "invalid platform in catalog: $platform"
}

# An attribute arriving from a GC root directory listing or the remote, on its
# way to becoming a path or a flake reference.
validate_attr() {
  local attr="$1"
  [[ -n "$attr" ]] || die "empty environment name"
  [[ "$attr" =~ $GOTG_ATTR_RE ]] || die "invalid environment name: $attr"
}
# UTC stamp for the metadata a person reads when choosing between two saves.
# Deliberately never an input to any decision, so making it deterministic for
# the tests costs nothing.
iso_now() { printf '%s' "${GOTG_NOW:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"; }

# The hard ceiling on a save bundle, enforced in both directions. This is the
# guard that catches a save glob which has quietly matched something enormous —
# a Harkinian .o2r is tens of megabytes, is rebuilt from the ROM, and must never
# be uploaded — and it is worth more than an exclude list, because it does not
# have to be complete to work.
saves_max_bytes() { printf '%s' "${GOTG_SAVES_MAX_BYTES:-67108864}"; }

# How many pre-pull archives survive here. The service keeps its own last few
# generations remotely; this is the local half of the same promise.
saves_keep() { printf '%s' "${GOTG_SAVES_KEEP:-3}"; }

# A server-controlled string that becomes a local path component. No slash and
# no dot-names is what rules out traversal. Control characters are rejected by
# name rather than requiring [[:print:]]: under LC_ALL=C that class rejects
# every multibyte character, and a Japanese dump name is a real member name —
# the server's own check (Python isprintable) accepts it.
# A member name may nest — a WiiU dump is fetched as its tree — so each
# slash-separated segment is validated on its own, mirroring the service.
validate_filename() {
  local name="$1"
  [[ -n "$name" && ${#name} -le 1024 ]] || die "invalid file name: $name"
  [[ "$name" != *[[:cntrl:]]* ]] || die "invalid file name: $name"
  local -a segments
  IFS=/ read -r -a segments <<<"$name"
  ((${#segments[@]} <= 8)) || die "invalid file name: $name"
  local segment
  for segment in "${segments[@]}"; do
    [[ -n "$segment" && ${#segment} -le 255 && "$segment" != .* ]] ||
      die "invalid file name: $name"
  done
}

# Titles are free-form text from the catalog and end up inside a generated
# script. Collapse them to one printable line so a newline cannot break out of
# the comment it sits in and become a command.
sanitize_title() {
  printf '%s' "$1" | tr -d '\000-\037\177' | cut -c1-120
}

# Make a string safe to use as the replacement in ${var//pattern/replacement}.
# Since bash 5.2 an unescaped '&' there means "the text that matched", exactly
# as in sed — so "Command & Conquer" would substitute the placeholder back into
# itself. Backslash escapes those, so it has to be escaped first.
escape_replacement() {
  local s="${1//\\/\\\\}"
  printf '%s' "${s//&/\\&}"
}

human_size() {
  local bytes="${1:-0}"
  awk -v b="$bytes" 'BEGIN {
    split("B KB MB GB TB", u, " ")
    i = 1
    while (b >= 1024 && i < 5) { b /= 1024; i++ }
    printf (i == 1 ? "%d %s" : "%.1f %s"), b, u[i]
  }'
}

# Replace <path> with what a command printed (or stdin did), all or nothing:
#
#   atomic_write <path> [mode] [-- <command...>]
#
# Without a command it copies stdin. A command is the safer form: its exit
# status decides whether the file is replaced, which a `producer | atomic_write`
# pipeline cannot do (the mv has happened before the producer's status is known).
# An empty mode means "as the umask makes any file".
#
# Two things only matter when they go wrong: the mode is set before the secret
# goes in (a config written 0600 after the fact was world-readable for as long
# as jq ran), and nothing is left behind when the writer fails (a `.tmp` that
# the next run trips over, or -- where the failure was not checked -- an empty
# file mv'd over a good one). The scratch file is
# mktemp'd beside the target, so two writers do not share it, and it is removed
# on every failure. The caller still decides what to say; this only returns 1.
atomic_write() {
  local path="$1" mode="" tmp
  shift
  if [[ $# -gt 0 && "$1" != "--" ]]; then
    mode="$1"
    shift
  fi
  [[ "${1:-}" != "--" ]] || shift
  tmp="$(mktemp "$path.XXXXXX")" || return 1
  if [[ -n "$mode" ]]; then
    chmod "$mode" "$tmp"
  else
    chmod "$(printf '%o' "$((0666 & ~$(umask)))")" "$tmp"
  fi
  if [[ $# -gt 0 ]]; then
    "$@" >"$tmp" || {
      rm -f "$tmp"
      return 1
    }
  else
    cat >"$tmp" || {
      rm -f "$tmp"
      return 1
    }
  fi
  mv -f "$tmp" "$path" || {
    rm -f "$tmp"
    return 1
  }
}

# Merge a JSON patch into the object in <path> (an empty object when there is
# no file), and write the result atomically:
#
#   json_merge_file <path> <patch-json> [mode]
#
# The same read-existing, `$existing + $patch`, chmod, mv sequence stood in
# config_patch, the saves setup, the saves journal and login, each reading the
# file its own way. A file that is not JSON, or a patch that is not, fails here
# and leaves the file exactly as it was.
json_merge_file() {
  local path="$1" patch="$2" mode="${3:-}" existing='{}'
  [[ ! -f "$path" ]] || existing="$(cat "$path")"
  # shellcheck disable=SC2016 # the quotes hold a jq program
  atomic_write "$path" "$mode" -- \
    jq -n --argjson existing "$existing" --argjson patch "$patch" '$existing + $patch'
}

# Run a command holding an exclusive lock on <lockfile>:
#
#   with_lock <lockfile> <wait-seconds|""> <timeout-message> <command...>
#
# An empty wait blocks for as long as it takes. When the wait runs out the
# caller's message is `die`d -- here, rather than as a status for the caller to
# test, because a command run on the left of `||` has errexit switched off for
# all of it, and a download or a queue write must not lose that.
#
# A release written by hand on every path that left the function was forgotten
# on one, and kept the lock for the rest of the process; a fixed fd number could
# collide with the progress pipe, which download.sh keeps on 9. The descriptor
# is allocated by bash here ({fd}), above 9, and closed in the one place.
#
# A command that dies ends the process, which drops the lock with it. Anything
# the command starts in the background inherits the descriptor and so keeps the
# lock, exactly as it did with the hand-written form.
with_lock() {
  local lockfile="$1" wait="$2" message="$3" fd rc
  shift 3
  mkdir -p "$(dirname "$lockfile")"
  exec {fd}>"$lockfile" || die "cannot open the lock $lockfile"
  if ! flock ${wait:+-w "$wait"} "$fd"; then
    exec {fd}>&-
    die "$message"
  fi
  # Not `"$@" || rc=$?`: see above. Under errexit a failing command ends the
  # process here, which drops the lock; with errexit off it falls through.
  "$@"
  rc=$?
  exec {fd}>&-
  return "$rc"
}
