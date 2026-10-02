#!/usr/bin/env bats
# `gotg launch --spec`: a game as Nix runs it (docs/nix-games.md).
#
# The spec is what a per-game flake output bakes in: the catalog entry, the
# server, where the bytes are, and the environment's store path. What a launch
# does with it is what `gotg play` does -- download, firmware, saves, danstick,
# bindings, overlay, session -- but nothing is looked up: no cached catalog, no
# GC root, no build. The environment is the one the spec names.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config

  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"
  export GOTG_KILLSWITCH=0

  add_game n64 "usa.zelda.z64" "rom bytes" "Zelda"
  # Anything that would build an environment says so here.
  stub_nix
  ENV_STORE="$TEST_TMP/store/gotg-env-env-n64"
  store_env "$ENV_STORE" "the spec's environment"
}

teardown() {
  stop_saves_service
}

# An environment as Nix builds it: a gotg-play and its manifests, somewhere
# that is not a GC root.
store_env() {
  local root="$1" says="$2"
  mkdir -p "$root/bin" "$root/share/gotg"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "%s launched with: $*"\n' "$says"
  } >"$root/bin/gotg-play"
  chmod +x "$root/bin/gotg-play"
  jq -n '{version: 1, name: "env-n64", saves: ["saves/**"], excludes: [], legacy: [], saveStates: false}' \
    >"$root/share/gotg/saves.json"
}

# The spec for one game, from the service's own catalog: what Nix embeds.
spec_for() {
  local id="$1" attr="${2:-env-n64}" env="${3:-$ENV_STORE}" server="${4:-$GOTG_SERVICE_URL}"
  local catalog
  catalog="$(curl -gfsS -H "Authorization: Bearer test-token" "$GOTG_SERVICE_URL/catalog")"
  jq -n --argjson c "$catalog" --arg id "$id" --arg attr "$attr" --arg env "$env" --arg server "$server" \
    '{version: 1, server: $server, files_urls: [($c.files_url // empty)], attr: $attr,
      env: $env, variant: "", game: ($c.games[] | select(.id == $id))}' >"$TEST_TMP/spec.json"
  printf '%s' "$TEST_TMP/spec.json"
}

@test "a launch from a spec runs the environment it names, with no catalog and nothing built" {
  gotg launch --spec "$(spec_for usa.zelda)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"the spec's environment launched with: $GOTG_GAMES_DIR/n64/"*"usa.zelda.z64"* ]]
  [ "$(cat "$GOTG_GAMES_DIR"/n64/*usa.zelda* 2>/dev/null | head -c 9)" = "rom bytes" ]
  [ ! -e "$GOTG_STATE_DIR/manifest.json" ]
  ! grep -q build "$NIX_LOG" 2>/dev/null
}

@test "the environment is the spec's, whatever a GC root says" {
  fake_env env-n64
  gotg launch --spec "$(spec_for usa.zelda)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"the spec's environment launched with:"* ]]
  [[ "$output" != *"env-n64 launched with:"* ]]
}

@test "emulator arguments after the spec reach the game" {
  gotg launch --spec "$(spec_for usa.zelda)" --fullscreen
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda.z64 --fullscreen"* ]]
}

@test "a spec naming no runnable environment is refused" {
  rm "$ENV_STORE/bin/gotg-play"
  gotg launch --spec "$(spec_for usa.zelda)"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no gotg-play"* ]]
}

@test "a spec whose game could not be a path is refused before anything is fetched" {
  local spec
  spec="$(spec_for usa.zelda)"
  jq '.game.id = "../../etc"' "$spec" >"$spec.bad"
  gotg launch --spec "$spec.bad"
  [ "$status" -ne 0 ]
  [ ! -e "$GOTG_GAMES_DIR/n64" ]
}

@test "a spec whose environment name is not one is refused" {
  local spec
  spec="$(spec_for usa.zelda)"
  jq '.attr = "../roots"' "$spec" >"$spec.bad"
  gotg launch --spec "$spec.bad"
  [ "$status" -ne 0 ]
}

@test "a spec from another server than the one logged into is refused, and no token goes there" {
  gotg launch --spec "$(spec_for usa.zelda env-n64 "$ENV_STORE" "https://elsewhere.example")"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"elsewhere.example"* ]]
  [[ "$stderr" == *"gotg login"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64" ]
}

@test "a spec that is not version 1 is refused" {
  local spec
  spec="$(spec_for usa.zelda)"
  jq '.version = 2' "$spec" >"$spec.bad"
  gotg launch --spec "$spec.bad"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"version"* ]]
}

@test "the overlay is told the spec's environment, for its saves" {
  unset GOTG_KILLSWITCH
  export WATCHER_LOG="$TEST_TMP/watcher.args"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "%%s\\n" "$*" >>"%s"\n' "$WATCHER_LOG"
  } >"$TEST_TMP/fake-killswitch"
  chmod +x "$TEST_TMP/fake-killswitch"
  export GOTG_KILLSWITCH_BIN="$TEST_TMP/fake-killswitch"
  gotg launch --spec "$(spec_for usa.zelda)"
  [ "$status" -eq 0 ]
  local i
  for i in $(seq 1 50); do [[ -s "$WATCHER_LOG" ]] && break; sleep 0.1; done
  [[ "$(cat "$WATCHER_LOG")" == *"--saves env-n64"* ]]
}

@test "nothing rebuilds the spec's environment, whoever asks" {
  # QA refreshes the environment it grades, install does too: neither may
  # point nix at a store path to build into.
  load_client_libs
  export GOTG_PINNED_ATTR=env-n64 GOTG_PINNED_ENV="$ENV_STORE"
  run env_build env-n64
  [ "$status" -eq 0 ]
  run env_refresh env-n64
  [ "$status" -eq 0 ]
  ! grep -q "build" "$NIX_LOG" 2>/dev/null
  rm "$ENV_STORE/bin/gotg-play"
  run env_build env-n64
  [ "$status" -ne 0 ]
}
