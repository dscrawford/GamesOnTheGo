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
  local candidate
  candidate="$(_gotg_named_flake)"
  # The source this client was built from (GOTG_OWN_FLAKE, set by the
  # package), before GitHub. Without it a client built from anything but
  # GitHub's head -- a Deck given a copy of a checkout -- built every
  # emulator environment from GitHub all the same: a client speaking
  # danstick, and environments still naming padmap.
  [[ -z "$candidate" && -f "${GOTG_OWN_FLAKE:-/nonexistent}/flake.nix" ]] && candidate="$GOTG_OWN_FLAKE"
  [[ -z "$candidate" ]] && candidate="$GOTG_REMOTE_FLAKE"
  printf '%s' "$candidate"
}

# Where a newer client comes from: `gotg sync`, and the hint the Steam
# launcher prints. Never the client's own source -- that is what it already is.
gotg_update_flake() {
  local candidate
  candidate="$(_gotg_named_flake)"
  [[ -z "$candidate" ]] && candidate="$GOTG_REMOTE_FLAKE"
  printf '%s' "$candidate"
}

# A flake somebody chose: GOTG_FLAKE, the config's `flake`, or a checkout in
# the usual place. Empty when none was.
_gotg_named_flake() {
  local candidate="${GOTG_FLAKE:-}"
  [[ -z "$candidate" ]] && candidate="$(config_get flake 2>/dev/null || true)"
  [[ -z "$candidate" && -f "$HOME/Documents/GOTG/flake.nix" ]] && candidate="$HOME/Documents/GOTG"
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
  # A source in the store is content-addressed: its path is its version.
  if [[ "$flake" == /nix/store/* ]]; then
    printf '%s' "$flake"
    return 0
  fi
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
    # `emulate`, reserved: the platform's own emulator, for a game whose
    # ordinary launch is something else. More and more of them are -- a native
    # port off a decompilation, which is better nearly always and not always:
    # a port is younger than the emulator, and when one of them has the bug
    # this is how you find out which.
    #
    # It is the platform environment itself rather than a file per game,
    # because that is precisely what it means: the way this platform runs a
    # game nobody wrote anything special for. A real <id>.emulate.nix, if one
    # ever existed, is checked above and wins -- this is the fallback.
    if [[ "$variant" == emulate ]]; then
      [[ -f "$GOTG_ENV_DIR/$platform.nix" ]] ||
        die "no emulator for platform '$platform', so there is nothing to fall back to"
      if [[ ! -f "$GOTG_ENV_DIR/games/$platform/$id.nix" ]]; then
        die "$id already runs on $platform's emulator; 'emulate' would change nothing"
      fi
      printf 'env-%s' "$platform"
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
  # The reserved one, offered only where it would do something. Which is not
  # "this game has an environment of its own": most of those are the platform's
  # emulator with settings added, and swapping one for the bare platform would
  # only drop the settings. The environment says whether it replaces the
  # emulator, with a marker its derivation carries, so this is the built root
  # rather than the source file -- and a game with nothing built yet is a game
  # nobody is choosing how to run. See nativePort in env/lib.nix.
  if [[ -e "$(env_root "env-$platform-${id/./_}")/share/gotg/native-port" &&
    -f "$GOTG_ENV_DIR/$platform.nix" &&
    ! -f "$GOTG_ENV_DIR/games/$platform/$id.emulate.nix" ]]; then
    printf 'emulate\n'
  fi
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
  pipedir="$(dialog_dir)"
  pipe="$pipedir/progress"
  mkfifo "$pipe"

  zenity_run --progress --title="GOTG" --text="Preparing $attr…" \
    --pulsate --auto-close <"$pipe" &
  zen_pid=$!
  exec 6>"$pipe"

  # A dialog that never came up is not a cancellation: build without it.
  if ! dialog_started "$zen_pid"; then
    exec 6>&-
    dialog_dir_remove "$pipedir"
    warn "the progress dialog could not start; building $attr without it"
    "$(nix_bin)" build "$ref" -o "$root" "$@"
    return
  fi

  "$(nix_bin)" build "$ref" -o "$root" "$@" &
  build_pid=$!

  local zrc
  while kill -0 "$build_pid" 2>/dev/null; do
    # The dialog is gone. zenity says why in its exit status: 1 is the
    # person closing or cancelling it, and that stops the build; anything
    # else -- an option it refused, a display it lost -- is the dialog's
    # problem and not a reason to lose an emulator build.
    if ! kill -0 "$zen_pid" 2>/dev/null; then
      wait "$zen_pid" 2>/dev/null
      zrc=$?
      exec 6>&-
      dialog_dir_remove "$pipedir"
      if [[ "$zrc" -eq 1 ]]; then
        kill "$build_pid" 2>/dev/null || true
        wait "$build_pid" 2>/dev/null || true
        die "build stopped: the progress dialog was cancelled"
      fi
      warn "the progress dialog went away (zenity exited $zrc); building $attr without it"
      wait "$build_pid" || status=$?
      return "$status"
    fi
    sleep "${GOTG_PROGRESS_TICK:-0.5}"
  done

  wait "$build_pid" || status=$?
  [[ "$status" -eq 0 ]] && printf '100\n' >&6
  exec 6>&-
  dialog_dir_remove "$pipedir"
  wait "$zen_pid" 2>/dev/null || true
  return "$status"
}

# nix's progress, as the lines the picker draws.
#
# A build run for the picker printed nothing while nix evaluated, fetched and
# compiled: the loader showed a clock, and a minute of evaluation looked the
# same as a hang. `--log-format internal-json` has nix say what it is doing;
# this turns that into
#
#     stage <eval|fetch|build> <done> <total> <what>
#
# (tab-separated; eval counts files, with no total) and passes a build's own
# output and nix's errors through as text, so a failure still reads on the
# TV. A figure is said once, not once per event: nix repeats itself.
# shellcheck disable=SC2016 # a jq program: its $ are jq's
_NIX_STAGES='
def store_name: tostring | sub("^/nix/store/[a-z0-9]+-"; "") | sub("\\.drv$"; "");
def quoted: (try (capture("\u0027(?<q>[^\u0027]+)\u0027").q) catch "");
def plain: gsub("\u001b\\[[0-9;]*[A-Za-z]"; "");
foreach inputs as $raw (
  {kinds: {}, eval: 0, what: "", last: null, out: null};
  .out = null
  | if ($raw | startswith("@nix ")) then
      (try ($raw[5:] | fromjson) catch null) as $e
      | if ($e | type) != "object" then .
        elif $e.action == "msg" then
          if ($e.msg // "" | startswith("evaluating file")) then
            .eval += 1
            | if (.eval % 40) == 1 then .out = "stage\teval\t\(.eval)\t0\t\(.what)" else . end
          elif ($e.level // 9) <= 1 then .out = ($e.msg | plain)
          else . end
        elif $e.action == "start" then
          if $e.type == 104 then .kinds[$e.id | tostring] = "build"
          elif $e.type == 103 then .kinds[$e.id | tostring] = "fetch"
          elif $e.type == 105 then .what = ($e.text // "" | quoted | store_name)
          elif $e.type == 108 then .what = ($e.fields[0] // "" | store_name)
          elif $e.type == 0 and ($e.text // "" | startswith("evaluating derivation")) then
            .what = ($e.text | quoted | split("#") | last)
            | .out = "stage\teval\t\(.eval)\t0\t\(.what)"
          else . end
        elif $e.action == "result" and $e.type == 105 and .kinds[$e.id | tostring] != null then
          .out = "stage\t\(.kinds[$e.id | tostring])\t\($e.fields[0])\t\($e.fields[1])\t\(.what)"
        elif $e.action == "result" and $e.type == 101 then .out = ($e.fields[0] // "" | tostring | plain)
        else . end
    else .out = ($raw | plain) end
  | if .out != null and .out == .last then .out = null
    elif .out != null then .last = .out
    else . end;
  .out | select(. != null)
)'

nix_stages() { jq -Rrn --unbuffered "$_NIX_STAGES"; }

# A nix command, drawn for the picker when it asked (GOTG_PROGRESS_LINES=1)
# and left exactly as nix prints it anywhere else. nix's own status is what
# returns, whatever the caller's pipefail: the filter always succeeds.
_nix_drawn() {
  if [[ "${GOTG_PROGRESS_LINES:-}" == "1" ]]; then
    local -a status
    "$(nix_bin)" "$@" --log-format internal-json -v 2>&1 >/dev/null | nix_stages >&2
    status=("${PIPESTATUS[@]}")
    return "${status[0]}"
  else
    "$(nix_bin)" "$@"
  fi
}

_env_build_failed() {
  local attr="$1" ref="$2"
  die "could not build $attr from $ref.
     Building an emulator is the one part of a launch that evaluates nix, and
     Steam's environment is a poor place to do it. From a terminal:
       nix build $ref -o $(env_root "$attr")
     A launch through Steam leaves its output in $GOTG_LOG_DIR."
}

# The flake reference an environment is built from, after checking a local
# flake is there to build it.
env_ref() {
  local attr="$1" flake
  flake="$(gotg_flake)"
  if flake_is_path "$flake"; then
    [[ -f "$flake/flake.nix" ]] ||
      die "no flake at $flake, so there is nothing to build $attr from.
     Point at your checkout with GOTG_FLAKE or the 'flake' key in $GOTG_CONFIG_FILE,
     or unset both to build straight from the repo over SSH."
  fi
  printf '%s#%s' "$flake" "$attr"
}

# A branch ref answers from nix's fetch cache for up to an hour, so a rebuild
# meant to pick up a change could quietly rebuild the old head.
_env_refresh_flag() {
  flake_is_path "$(gotg_flake)" || printf -- '--refresh'
}

# Evaluate an environment without building it: seconds, where the build can
# be minutes, and the part that fails when a definition is broken. The
# picker's install asks this first so a broken environment still stops it
# before a download that can run to tens of gigabytes.
env_evaluate() {
  local attr="$1" ref
  ref="$(env_ref "$attr")"
  local -a refresh=()
  read -ra refresh <<<"$(_env_refresh_flag)"
  _nix_drawn path-info --derivation "$ref" ${refresh[@]+"${refresh[@]}"}
}

# Build an environment and keep it alive with a GC root.
env_build() {
  local attr="$1" ref root
  ref="$(env_ref "$attr")"
  root="$(env_root "$attr")"
  mkdir -p "$GOTG_ROOTS_DIR"

  local -a refresh=()
  read -ra refresh <<<"$(_env_refresh_flag)"

  [[ -n "${GOTG_BUILD_QUIET:-}" ]] ||
    log "building $attr from $ref — the first launch on a platform compiles its emulator"
  if ! is_tty && has_display && have_zenity; then
    _env_build_zenity "$ref" "$root" "$attr" ${refresh[@]+"${refresh[@]}"} || _env_build_failed "$attr" "$ref"
  else
    _nix_drawn build "$ref" -o "$root" ${refresh[@]+"${refresh[@]}"} || _env_build_failed "$attr" "$ref"
  fi

  env_is_built "$attr" ||
    die "built $attr but $(env_bin "$attr") is missing — check src/client/env for that platform"
  printf '%s\n' "$(env_build_key)" >"$root.by"
}

# The environment build `gotg install` started beside the download, if any:
# waited for by whatever needs the built root (a recipe, the launcher).
GOTG_ENV_BUILD_PID=""

env_build_wait() {
  [[ -n "$GOTG_ENV_BUILD_PID" ]] || return 0
  local pid="$GOTG_ENV_BUILD_PID"
  GOTG_ENV_BUILD_PID=""
  wait "$pid" || die "the game is downloaded, but its emulator did not build (above)"
}

# What a root is built by: this client, and -- from a clean checkout, where
# it costs a git call -- the tree it came from. Written beside the root when
# it is built, compared on every launch. A URL flake's revision is not in it
# on purpose: knowing it means asking the network, and the client's own
# store path already changes with every upgrade.
env_build_key() {
  local flake key="$GOTG_ROOT" print
  flake="$(gotg_flake)"
  if flake_is_path "$flake"; then
    print="$(flake_fingerprint "$flake")"
    if [[ -z "$print" ]]; then
      # A checkout with edits in it. The fingerprint is empty then -- it
      # means "build and see" to sync -- and an empty one here was the same
      # empty string every launch, so a root built from a dirty tree matched
      # every later dirty tree and was never rebuilt. A day of uncommitted
      # fixes to Four Swords Adventures launched the environment from the
      # night before, every time. The edits themselves are the fingerprint:
      # what a flake of this checkout would build is the commit plus them.
      print="dirty:$(git -C "$flake" rev-parse HEAD 2>/dev/null):$(
        git -C "$flake" diff HEAD 2>/dev/null | sha256sum | cut -c1-16
      )"
    fi
    key+="|$print"
  fi
  printf '%s' "$key"
}

# Whether a built root was built by this client from this tree -- the one
# thing env_ensure checks before it rebuilds. Asked by `complete ready` too:
# a root that is about to be rebuilt is not ready, whatever is on disk.
env_is_current() {
  local by
  by="$(env_root "$1").by"
  [[ ! -f "$by" || "$(cat "$by")" == "$(env_build_key)" ]]
}

env_ensure() {
  local attr="$1"
  if ! env_is_built "$attr"; then
    env_build "$attr"
    return
  fi
  # Built by an older gotg, or from an older tree: rebuilt before it runs.
  # A root only ever got built when it was missing, so a client upgraded
  # under a person kept launching the environments the old one had built --
  # the Deck after every `nix profile upgrade`, a checkout after every pull
  # -- until somebody thought to run `gotg sync`. Best effort, like sync:
  # a rebuild that fails leaves what is here, which still runs.
  if ! env_is_current "$attr"; then
    log "$attr was built by an older gotg; rebuilding it"
    env_refresh "$attr" || warn "could not rebuild $attr — launching the build already here"
  fi
}

# Rebuild something that is already here, tolerating failure. Used where the
# point is to pick up a definition that has moved: if the flake cannot be
# reached, what is already built still runs, and refusing to continue would be
# worse than being one version behind. The subshell is what makes env_build's
# die local — it ends the attempt rather than the command.
env_refresh() {
  # No dialog, ever. A refresh always has something runnable already -- its
  # whole point is picking up a definition that moved -- so a progress window
  # is news about work nobody is waiting on, and it lands in front of a game
  # that is about to start. This is the one that kept appearing: every launch
  # after a client upgrade goes through here (see env_ensure), and from Steam
  # there is no terminal, so the dialog was what a person saw.
  #
  # A first build is the other case and keeps its dialog: nothing runs yet,
  # the compile can take minutes, and a blank screen for that long is worse
  # than a window.
  (
    export GOTG_NO_DIALOG=1
    env_build "$1"
  )
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
