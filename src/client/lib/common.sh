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
GOTG_SAVES_DIR="$GOTG_STATE_DIR/saves"

# Where games land. One directory per platform, mirroring the server layout.
GOTG_GAMES_DIR="${GOTG_GAMES_DIR:-$HOME/Games}"
GOTG_PARTIAL_DIR="$GOTG_GAMES_DIR/.gotg-partial"

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

# An attribute arriving from a GC root directory listing or the remote, on its
# way to becoming a path or a flake reference.
validate_attr() {
  local attr="$1"
  [[ -n "$attr" ]] || die "empty environment name"
  [[ "$attr" =~ $GOTG_ATTR_RE ]] || die "invalid environment name: $attr"
}
# UTC stamp for the metadata a person reads when choosing between two saves.
# Deliberately never an input to any decision — see docs/saves.md — so making it
# deterministic for the tests costs nothing.
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
