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

# Where games land. One directory per platform, mirroring the server layout.
GOTG_GAMES_DIR="${GOTG_GAMES_DIR:-$HOME/Games}"
GOTG_PARTIAL_DIR="$GOTG_GAMES_DIR/.gotg-partial"

# The entry-id contract, shared with the importer.
GOTG_ID_RE='^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$'
# Platform slugs name a directory under ~/Games, so they are checked too.
GOTG_PLATFORM_RE='^[a-z0-9][a-z0-9_-]*$'

log() { printf '%s\n' "$*" >&2; }
warn() { printf 'warning: %s\n' "$*" >&2; }

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# True when there is a person watching a terminal, false under Steam or cron.
is_tty() { [[ -t 1 && -t 2 ]]; }

# True when a graphical progress dialog can be shown.
has_display() { [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
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

# Reject anything that could climb out of the games directory.
validate_remote_path() {
  local path="$1"
  [[ "$path" == /* ]] || die "remote path must be absolute: $path"
  [[ "$path" != *..* ]] || die "remote path may not contain '..': $path"
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

# Percent-encode each path segment but keep the separators. Game filenames are
# full of spaces, apostrophes and parentheses.
url_encode_path() {
  jq -rn --arg p "$1" '$p | split("/") | map(@uri) | join("/")'
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
