#!/usr/bin/env bats
# `gotg port`: a game's portable build, built from the library and kept on the
# real service, then listed, pulled and dropped.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/n64"
  : >"$GOTG_ENV_DIR/n64.nix"
  : >"$GOTG_ENV_DIR/games/n64/usa.zelda.rando.nix"
  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  use_library
}

teardown() {
  stop_saves_service
}

@test "push builds the portable output and puts it on the server" {
  GOTG_DEVICE_NAME=the-deck gotg port push usa.zelda rando
  [ "$status" -eq 0 ]
  grep -q "build $GOTG_LIBRARY#n64.usa.zelda.rando.gotgPortable -o" "$NIX_LOG"
  [[ "$stderr" == *"n64.usa.zelda.rando is on the server"* ]]

  run curl -fsS -H "Authorization: Bearer test-token" "$GOTG_SERVICE_URL/ports/n64.usa.zelda.rando"
  [ "$status" -eq 0 ]
  [ "$(jq -r .device <<<"$output")" = the-deck ]
  [[ "$(jq -r .sha256 <<<"$output")" == "$(sha256sum "$TEST_TMP/store/gotg-portable-n64.usa.zelda.rando.run" | cut -d' ' -f1)" ]]
}

@test "list, pull and rm" {
  gotg port push usa.zelda
  [ "$status" -eq 0 ]
  gotg port list
  [ "$status" -eq 0 ]
  [[ "$output" == n64.usa.zelda$'\t'* ]]

  cd "$TEST_TMP"
  gotg port pull usa.zelda
  [ "$status" -eq 0 ]
  [ -x "$TEST_TMP/gotg-n64.usa.zelda.run" ]
  cmp "$TEST_TMP/gotg-n64.usa.zelda.run" "$TEST_TMP/store/gotg-portable-n64.usa.zelda.run"

  gotg port rm usa.zelda
  [ "$status" -eq 0 ]
  gotg port pull usa.zelda -o "$TEST_TMP/again.run"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"nothing has been pushed for n64.usa.zelda; on a machine with Nix: gotg port push usa.zelda"* ]]
  [ ! -e "$TEST_TMP/again.run" ]
}

@test "a build that fails uploads nothing" {
  NIX_FAIL_PORTABLE=1 gotg port push usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"could not build n64.usa.zelda.gotgPortable"* ]]
  run curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer test-token" "$GOTG_SERVICE_URL/ports/n64.usa.zelda"
  [ "$output" = 404 ]
}

@test "without a login there is nothing to push to" {
  rm "$GOTG_CONFIG_DIR/api.json"
  gotg port push usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"run: gotg login"* ]]
}
