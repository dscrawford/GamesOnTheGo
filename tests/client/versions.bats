#!/usr/bin/env bats
# Which version of a game a launch runs.
#
# A Switch game is a base and a pile of updates, and the update decides what
# the game *is*: an exefs mod is machine code written against one executable.
# Tears of the Kingdom is why this exists — the mod supported up to 1.4.2, the
# library had 1.4.3, and the answer was a crash a minute into play with nothing
# on screen to say why. So the rules here are about never letting that be
# silent: what is installed, what a mod can take, and a refusal in words when
# the two cannot meet.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  load_client_libs
  source "$GOTG_LIB/versions.sh"

  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/switch"
  : >"$GOTG_ENV_DIR/switch.nix"
  fake_env env-switch
}

teardown() { stop_saves_service; }

# A game on disk with the updates named the way the importer names them.
#
# A catalog entry that carries extras installs as a *directory* named by its id
# — which is the shape this feature reads — so the entry is published with one,
# and the directory is made locally before anything launches so no download is
# attempted.
installed_with() {
  local dir="$GOTG_GAMES_DIR/switch/world.zelda"
  local lib="$SERVICE_LIBRARY_DIR/switch/world.zelda"
  mkdir -p "$dir/extras" "$lib/extras"
  printf 'game' >"$dir/world.zelda.nsp"
  printf 'game' >"$lib/world.zelda.nsp"

  local files="[]" version name
  files="$(_zelda_file "$files" "world.zelda.nsp" "$lib/world.zelda.nsp")"
  for version in "$@"; do
    name="extras/update_${version}-sxs_v1.nsp"
    printf 'update' >"$dir/$name"
    printf 'update' >"$lib/$name"
    files="$(_zelda_file "$files" "$name" "$lib/$name")"
  done

  jq -n --argjson f "$files" \
    '{handler: "single_archive", title: "Zelda", files: $f}' |
    curl -gfsS -X PUT -H "Authorization: Bearer index-token" \
      --data-binary @- "$GOTG_SERVICE_URL/catalog/switch/world.zelda" >/dev/null
  gotg refresh
  GAME="$(manifest_find world.zelda)"
}

# One file row, the way the indexer writes them: real hash, real size, the
# server's own path.
_zelda_file() {
  local files="$1" name="$2" path="$3" sha size mtime
  sha="$(sha256sum "$path" | cut -d' ' -f1)"
  size="$(stat -c '%s' "$path")"
  mtime="$(stat -c '%Y' "$path")"
  jq --arg n "$name" --arg p "$path" --arg sha "$sha" \
    --argjson s "$size" --argjson m "$mtime" \
    '. + [{name: $n, path: $p, size_bytes: $s, mtime: $m, sha256: $sha}]' <<<"$files"
}

# An environment that says which version its mod was built for. The variant's
# own file too, since that is what makes `gotg play <id> 60fps` resolve to it.
env_with_ceiling() {
  : >"$GOTG_ENV_DIR/games/switch/world.zelda.60fps.nix"
  fake_env "$1"
  local manifest="$GOTG_ROOTS_DIR/$1/share/gotg/saves.json"
  jq --arg v "$2" '. + {gameVersionMax: $v}' "$manifest" >"$manifest.tmp"
  mv "$manifest.tmp" "$manifest"
}

# --- what is here -----------------------------------------------------------

@test "a game with no updates has no versions to choose between" {
  installed_with
  run versions_names "$GAME"
  [ -z "$output" ]
  run versions_newest "$GAME"
  [ -z "$output" ]
}

@test "updates are listed newest first, by version rather than by spelling" {
  # 1.4.10 is newer than 1.4.9, which sorting as text gets backwards.
  installed_with 1.2.1 1.4.9 1.4.10 1.4.2
  run versions_names "$GAME"
  [ "$(printf '%s' "$output" | tr '\n' ' ')" = "1.4.10 1.4.9 1.4.2 1.2.1" ]
  run versions_newest "$GAME"
  [ "$output" = "1.4.10" ]
}

@test "a file that is not an update is not a version" {
  installed_with 1.4.2
  printf 'notes' >"$GOTG_GAMES_DIR/switch/world.zelda/extras/readme.nfo"
  run versions_names "$GAME"
  [ "$output" = "1.4.2" ]
}

# --- which one runs ---------------------------------------------------------

@test "the newest runs when nothing says otherwise" {
  installed_with 1.2.1 1.4.3
  run versions_resolve "$GAME" env-switch
  [ "$output" = "1.4.3" ]
}

@test "an asked-for version is the one that runs" {
  installed_with 1.2.1 1.4.3
  run versions_resolve "$GAME" env-switch 1.2.1
  [ "$output" = "1.2.1" ]
}

@test "asking for a version that is not here is refused, with what is" {
  # Silently running a different version is how somebody spends an evening
  # wondering why a mod does nothing.
  installed_with 1.4.3
  run ! versions_resolve "$GAME" env-switch 1.2.1
  [[ "$output" == *"no version 1.2.1"* ]]
  [[ "$output" == *"1.4.3"* ]]
}

@test "a mod takes the newest version it was built for, and says so" {
  installed_with 1.2.1 1.4.2 1.4.3
  env_with_ceiling env-switch-world_zelda-60fps 1.4.2
  run --separate-stderr versions_resolve "$GAME" env-switch-world_zelda-60fps
  [ "$output" = "1.4.2" ]
  [[ "$stderr" == *"built for 1.4.2 or older"* ]]
}

@test "a mod whose ceiling is the newest version says nothing at all" {
  installed_with 1.4.2
  env_with_ceiling env-switch-world_zelda-60fps 1.4.2
  run --separate-stderr versions_resolve "$GAME" env-switch-world_zelda-60fps
  [ "$output" = "1.4.2" ]
  [ -z "$stderr" ]
}

@test "a mod with nothing old enough refuses the launch rather than crashing later" {
  installed_with 1.4.3
  env_with_ceiling env-switch-world_zelda-60fps 1.4.2
  run ! versions_resolve "$GAME" env-switch-world_zelda-60fps
  [[ "$output" == *"needs version 1.4.2 or older"* ]]
}

@test "an environment that is not a mod has no ceiling and no opinion" {
  installed_with 1.4.3
  run versions_env_ceiling env-switch
  [ -z "$output" ]
}

# --- on the command line ----------------------------------------------------

@test "versions lists what is here and marks what runs" {
  installed_with 1.2.1 1.4.3
  gotg versions world.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"* 1.4.3 (newest)"* ]]
  [[ "$output" == *"1.2.1"* ]]
}

@test "versions says when there is nothing to choose between" {
  installed_with
  gotg versions world.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no updates here"* ]]
}

@test "the picker can ask which versions there are" {
  installed_with 1.2.1 1.4.3
  gotg complete versions switch/world.zelda
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | tr '\n' ' ')" = "*1.4.3 1.2.1" ]
}

@test "the picker is told which one a mod would run" {
  installed_with 1.2.1 1.4.2 1.4.3
  env_with_ceiling env-switch-world_zelda-60fps 1.4.2
  gotg complete versions switch/world.zelda 60fps
  [[ "$output" == *"*1.4.2"* ]]
  [[ "$output" != *"*1.4.3"* ]]
}

# --- and into the launch ----------------------------------------------------

@test "the version a launch settled on reaches the environment" {
  installed_with 1.2.1 1.4.3
  # An environment that reports what it was given rather than running a game.
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "version=${GOTG_GAME_VERSION:-none}"\n'
  } >"$GOTG_ROOTS_DIR/env-switch/bin/gotg-play"
  chmod +x "$GOTG_ROOTS_DIR/env-switch/bin/gotg-play"

  gotg play world.zelda --version 1.2.1
  [ "$status" -eq 0 ]
  [[ "$output" == *"version=1.2.1"* ]]

  gotg play world.zelda
  [[ "$output" == *"version=1.4.3"* ]]
}

@test "a version that is not here stops the launch before the emulator" {
  installed_with 1.4.3
  gotg play world.zelda --version 1.2.1
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no version 1.2.1"* ]]
}
