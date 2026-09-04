# shellcheck shell=bash
# Which environment runs a game, and getting it built.
#
# An environment is a nix derivation — see src/client/env — that wraps one emulator
# together with the arguments and settings a platform, or one particular game,
# needs. It is built into a GC-rooted symlink under ~/.local/state/gotg/roots and
# always exposes the same binary, bin/gotg-play.
#
# Working out *which* environment a game wants never evaluates nix: it comes from
# the catalog entry and the names of the files in src/client/env. That keeps `list`,
# `info` and a launch of anything already built working offline, and confines nix
# to the one case that genuinely needs it — an environment that is not here yet.

# Where environments build from when no checkout is configured: the repo
# itself, reached the same way the client itself was. What makes `gotg play`
# work out of the box on a machine that has only ever run `nix run`.
#
# `github:` rather than git+ssh or git+https, because it is the one form nix
# can authenticate on its own. Its access-tokens setting applies to github:
# refs only — the git+https fetcher shells out to git and asks for a username,
# so it needs a credential helper configured, and git+ssh needs a key on the
# repo. Someone handed a read-only token has neither, and this is the whole
# path that makes them unnecessary:
#
#   NIX_CONFIG="extra-access-tokens = github.com=<token>" gotg play <id>
GOTG_REMOTE_FLAKE="${GOTG_REMOTE_FLAKE:-github:dscrawford/GamesOnTheGo}"

# The flake the environments are built from: explicit env, then the config
# key, then a checkout in the usual place, then the repo over the network.
gotg_flake() {
  local candidate="${GOTG_FLAKE:-}"
  [[ -z "$candidate" ]] && candidate="$(config_get flake 2>/dev/null || true)"
  [[ -z "$candidate" && -f "$HOME/Documents/GOTG/flake.nix" ]] && candidate="$HOME/Documents/GOTG"
  [[ -z "$candidate" ]] && candidate="$GOTG_REMOTE_FLAKE"
  printf '%s' "$candidate"
}

# A path is checked for a flake.nix before nix is asked; a URL cannot be, and
# nix's own error is the right one when it is unreachable.
flake_is_path() { [[ "$1" != *://* && "$1" != github:* && "$1" != flake:* ]]; }

# A seam for the tests, which run where there is no nix.
nix_bin() { printf '%s' "${GOTG_NIX:-nix}"; }

# What the flake is right now, cheaply: the checkout's commit, or the url
# flake's resolved revision. Empty when it cannot be known or the checkout
# has uncommitted edits — either of which means "build and see". This is
# what lets sync tell an unchanged flake apart from one worth an evaluation,
# without paying for the evaluation to find out.
flake_fingerprint() {
  local flake="$1" rev
  if flake_is_path "$flake"; then
    [[ -z "$(git -C "$flake" status --porcelain 2>/dev/null)" ]] || return 0
    rev="$(git -C "$flake" rev-parse HEAD 2>/dev/null)" || return 0
  else
    rev="$("$(nix_bin)" flake metadata --refresh --json "$flake" 2>/dev/null | jq -r '.revision // empty' 2>/dev/null)" || return 0
  fi
  printf '%s' "$rev"
}

# Per-game overrides are the one thing a person tweaks per machine, so a copy in
# the config directory wins over the one shipped in the store.
overrides_json() {
  if [[ -f "$GOTG_CONFIG_DIR/overrides.json" ]]; then
    printf '%s/overrides.json' "$GOTG_CONFIG_DIR"
  else
    printf '%s/overrides.json' "$GOTG_DATA"
  fi
}

# Per-game overrides, looked up by "platform/id" first then bare id.
override_field() {
  local game="$1" field="$2" id platform
  id="$(manifest_field "$game" id)"
  platform="$(manifest_field "$game" platform)"
  jq -r --arg k1 "$platform/$id" --arg k2 "$id" --arg f "$field" \
    '(.[$k1][$f] // .[$k2][$f]) // empty' "$(overrides_json)"
}

# The flake attribute for a game: its own environment when src/client/env has a file
# for it, otherwise its platform's. A dot is an attribute path separator in a
# flake reference and an id always holds one, so it becomes an underscore — the
# same rule src/client/env/default.nix names the attributes by.
env_attr() {
  local game="$1" variant="${2:-}" id platform
  id="$(manifest_field "$game" id)"
  platform="$(manifest_field "$game" platform)"
  validate_id "$id"
  validate_platform "$platform"

  # A named variant of one game: a different engine, a mod, a second way to run
  # it. The file is "<id>.<variant>.nix", which cannot be confused with a plain
  # game file because an id holds exactly one dot.
  if [[ -n "$variant" ]]; then
    [[ "$variant" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "invalid variant name: $variant"
    if [[ -f "$GOTG_ENV_DIR/games/$platform/$id.$variant.nix" ]]; then
      printf 'env-%s-%s-%s' "$platform" "${id/./_}" "$variant"
      return 0
    fi
    local available
    available="$(env_variants "$platform" "$id")"
    die "no '$variant' variant of $id.
     ${available:-There are no variants of this game.}
     A variant is src/client/env/games/$platform/$id.<name>.nix — then: gotg sync"
  fi

  if [[ -f "$GOTG_ENV_DIR/games/$platform/$id.nix" ]]; then
    printf 'env-%s-%s' "$platform" "${id/./_}"
  elif [[ -f "$GOTG_ENV_DIR/$platform.nix" ]]; then
    printf 'env-%s' "$platform"
  else
    die "no environment for platform '$platform'.
     Add one at src/client/env/$platform.nix — the existing ones are three lines
     each — then run: gotg sync"
  fi
}

# The variants a game has, for an error message worth reading.
# One variant name per line, and nothing at all when a game has none. Separate
# from the sentence below it because completion wants the names and a person
# wants the sentence.
env_variant_names() {
  local platform="$1" id="$2" file name
  for file in "$GOTG_ENV_DIR/games/$platform/$id".*.nix; do
    [[ -e "$file" ]] || continue
    name="$(basename "$file" .nix)"
    name="${name#"$id".}"
    # Only what env_attr accepts: a stray second dot in a filename makes a
    # name play rejects, and advertising it in info or completion is a lie.
    [[ "$name" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || continue
    printf '%s\n' "$name"
  done
}

env_variants() {
  local names=()
  mapfile -t names < <(env_variant_names "$1" "$2")
  [[ ${#names[@]} -gt 0 ]] || return 0
  printf 'Available: %s' "${names[*]}"
}

env_root() { printf '%s/%s' "$GOTG_ROOTS_DIR" "$1"; }
env_bin() { printf '%s/bin/gotg-play' "$(env_root "$1")"; }
env_is_built() { [[ -x "$(env_bin "$1")" ]]; }

# The writable directory an environment keeps its settings and saves in. The
# generated wrapper computes the same path and prefers GOTG_ENV_STATE, which
# cmd_play exports from here, so the two cannot drift apart.
env_state_dir() {
  validate_attr "$1"
  printf '%s/%s' "${GOTG_ENV_STATE_DIR:-$GOTG_STATE_DIR/env}" "$1"
}

# What an environment says is worth backing up, emitted by its derivation and
# read straight from the GC root — a file read, not a nix evaluation.
env_saves_manifest() {
  validate_attr "$1"
  printf '%s/share/gotg/saves.json' "$(env_root "$1")"
}

# Which ares console this environment's bindings belong to, if any. Absent for
# an environment whose bindings are not generated.
env_pads_manifest() {
  validate_attr "$1"
  printf '%s/share/gotg/pads.json' "$(env_root "$1")"
}

# nix can take a long time the first time a platform is used. Under Steam there
# is no terminal for it to say so in, and a game that shows no window for twenty
# minutes reads as a crash, so pulse a dialog for as long as the build runs.
_env_build_zenity() {
  local ref="$1" root="$2" attr="$3"
  shift 3
  local pipedir pipe build_pid zen_pid status=0
  pipedir="$(mktemp -d)"
  pipe="$pipedir/progress"
  mkfifo "$pipe"

  zenity --progress --title="GOTG" --text="Preparing $attr…" \
    --pulsate --auto-close <"$pipe" &
  zen_pid=$!
  exec 6>"$pipe"

  "$(nix_bin)" build "$ref" -o "$root" "$@" &
  build_pid=$!

  while kill -0 "$build_pid" 2>/dev/null; do
    # The dialog is gone: either the user cancelled, or zenity never started.
    if ! kill -0 "$zen_pid" 2>/dev/null; then
      kill "$build_pid" 2>/dev/null || true
      wait "$build_pid" 2>/dev/null || true
      exec 6>&-
      rm -rf "$pipedir"
      die "build stopped: the progress dialog closed (cancelled, or zenity could not run)"
    fi
    sleep "${GOTG_PROGRESS_TICK:-0.5}"
  done

  wait "$build_pid" || status=$?
  [[ "$status" -eq 0 ]] && printf '100\n' >&6
  exec 6>&-
  rm -rf "$pipedir"
  wait "$zen_pid" 2>/dev/null || true
  return "$status"
}

_env_build_failed() {
  local attr="$1" ref="$2"
  die "could not build $attr from $ref.
     Building an emulator is the one part of a launch that evaluates nix, and
     Steam's environment is a poor place to do it. From a terminal:
       nix build $ref -o $(env_root "$attr")
     A launch through Steam leaves its output in $GOTG_LOG_DIR."
}

# Build an environment and keep it alive with a GC root.
env_build() {
  local attr="$1" flake ref root
  flake="$(gotg_flake)"
  if flake_is_path "$flake"; then
    [[ -f "$flake/flake.nix" ]] ||
      die "no flake at $flake, so there is nothing to build $attr from.
     Point at your checkout with GOTG_FLAKE or the 'flake' key in $GOTG_CONFIG_FILE,
     or unset both to build straight from the repo over SSH."
  fi

  ref="$flake#$attr"
  root="$(env_root "$attr")"
  mkdir -p "$GOTG_ROOTS_DIR"

  # A branch ref answers from nix's fetch cache for up to an hour, so a
  # rebuild meant to pick up a change could quietly rebuild the old head.
  local -a refresh=()
  flake_is_path "$flake" || refresh=(--refresh)

  [[ -n "${GOTG_BUILD_QUIET:-}" ]] ||
    log "building $attr from $ref — the first launch on a platform compiles its emulator"
  if ! is_tty && has_display && command -v zenity >/dev/null 2>&1; then
    _env_build_zenity "$ref" "$root" "$attr" "${refresh[@]}" || _env_build_failed "$attr" "$ref"
  else
    "$(nix_bin)" build "$ref" -o "$root" "${refresh[@]}" || _env_build_failed "$attr" "$ref"
  fi

  env_is_built "$attr" ||
    die "built $attr but $(env_bin "$attr") is missing — check src/client/env for that platform"
}

env_ensure() {
  local attr="$1"
  env_is_built "$attr" || env_build "$attr"
}

# Rebuild something that is already here, tolerating failure. Used where the
# point is to pick up a definition that has moved: if the flake cannot be
# reached, what is already built still runs, and refusing to continue would be
# worse than being one version behind. The subshell is what makes env_build's
# die local — it ends the attempt rather than the command.
env_refresh() {
  (env_build "$1")
}

# The file handed to the emulator. Directory games need a glob (Wii U wants the
# .rpx inside code/), single-file games are just themselves.
resolve_target() {
  local game="$1" path pattern match
  path="$(game_installed_path "$game")" ||
    die "not installed: $(game_local_path "$game")"

  pattern="$(override_field "$game" target)"
  if [[ -z "$pattern" && -d "$path" ]]; then
    # A recipe's bundle: the game is <id>.<ext> inside, its extras beside it.
    local id bundled
    id="$(manifest_field "$game" id)"
    bundled=("$path/$id".*)
    if [[ -f "${bundled[0]}" ]]; then
      printf '%s' "${bundled[0]}"
      return 0
    fi
  fi
  if [[ -z "$pattern" || ! -d "$path" ]]; then
    printf '%s' "$path"
    return 0
  fi

  # shellcheck disable=SC2086 # the pattern is a glob on purpose
  match="$(find "$path" -ipath "$path/$pattern" -type f 2>/dev/null | head -n1)"
  [[ -n "$match" ]] || die "no file matching '$pattern' under $path"
  printf '%s' "$match"
}
