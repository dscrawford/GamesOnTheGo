# shellcheck shell=bash
# Which environment runs a game, and where it is.
#
# An environment is a nix derivation — see src/client/env — that wraps one emulator
# together with the arguments and settings a platform, or one particular game,
# needs, and always exposes the same binary, bin/gotg-play. Nix builds it as a
# dependency of the game's library output (docs/nix-games.md); nothing here
# builds one.
#
# Working out *which* environment a game wants never evaluates nix: it comes from
# the catalog entry and the names of the files in src/client/env -- the same rule
# as gotg.lib.catalog's, which checks.resolver holds them to. That keeps `list`,
# `info`, saves and configure working offline.

# A seam for the tests, which run where there is no nix.
nix_bin() { printf '%s' "${GOTG_NIX:-nix}"; }

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

# Where an environment is. For the one a `gotg launch` spec names
# (cmd-launch.sh), the store path Nix built it at. Otherwise, the one a game
# built here through the library names in its spec (share/gotg/spec.json under
# games/) -- which is what lets saves, configure, steam and controllers find an
# environment with no evaluation. Failing both, the GC root an older client
# built.
env_root() {
  if [[ -n "${GOTG_PINNED_ATTR:-}" && "$1" == "$GOTG_PINNED_ATTR" ]]; then
    printf '%s' "$GOTG_PINNED_ENV"
    return 0
  fi
  local spec env
  for spec in "$GOTG_STATE_DIR"/games/*/share/gotg/spec.json; do
    [[ -f "$spec" ]] || continue
    env="$(jq -r --arg a "$1" 'select(.attr == $a) | .env // empty' "$spec" 2>/dev/null)"
    if [[ -n "$env" && -d "$env" ]]; then
      printf '%s' "$env"
      return 0
    fi
  done
  printf '%s/%s' "$GOTG_ROOTS_DIR" "$1"
}
# Every environment built here, one attr per line: those the games' specs name,
# then any left in the roots directory from before libraries. What `--all`
# walks (saves, controllers) and what sync keeps GL for.
env_built_attrs() {
  local spec root name
  {
    for spec in "$GOTG_STATE_DIR"/games/*/share/gotg/spec.json; do
      [[ -f "$spec" ]] || continue
      jq -r '.attr // empty' "$spec" 2>/dev/null || true
    done
    for root in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$root" ]] || continue
      name="$(basename "$root")"
      printf '%s\n' "$name"
    done
  } | while IFS= read -r name; do
    # An environment's name, and nothing else: the build-key file beside
    # every old root (`env-n64.by`) starts with env- too.
    [[ "$name" =~ $GOTG_ATTR_RE && -e "$(env_root "$name")" ]] && printf '%s\n' "$name"
  done | awk '!seen[$0]++'
}
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

# The environment build `gotg install` started beside the download, if any:
# waited for by whatever needs the built root (a recipe, the launcher).
GOTG_ENV_BUILD_PID=""

env_build_wait() {
  [[ -n "$GOTG_ENV_BUILD_PID" ]] || return 0
  local pid="$GOTG_ENV_BUILD_PID"
  GOTG_ENV_BUILD_PID=""
  wait "$pid" || die "the game is downloaded, but its emulator did not build (above)"
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
