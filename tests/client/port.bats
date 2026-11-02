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

# --- the worker: browser builds the web app asks for ----------------------------

web_api() {
  curl -sS -H "Authorization: Bearer test-token" "$@"
}

ask_for_web_build() {
  # The service lists a game as buildable for the browser by its builder file.
  mkdir -p "$TEST_TMP/service-web/n64"
  : >"$TEST_TMP/service-web/n64/usa.zelda.nix"
  web_api -X POST "$GOTG_SERVICE_URL/ports/n64.usa.zelda/web" >/dev/null
}

@test "worker builds what was asked for against the ROM, and pushes it" {
  ask_for_web_build
  GOTG_DEVICE_NAME=the-desk gotg port worker --once
  [ "$status" -eq 0 ]
  grep -q "build $GOTG_LIBRARY#n64.usa.zelda.gotgWeb -o" "$NIX_LOG"
  [[ "$stderr" == *"n64.usa.zelda is on the server"* ]]

  run web_api "$GOTG_SERVICE_URL/ports/n64.usa.zelda/web"
  [ "$(jq -r .build.device <<<"$output")" = the-desk ]
  [ "$(jq -r .request <<<"$output")" = null ]
  # Built from the ROM the catalog has, downloaded as an install is.
  run web_api "$GOTG_SERVICE_URL/ports/n64.usa.zelda/web/file"
  [ "$output" = "built from $(printf rom | sha256sum | cut -d' ' -f1)" ]

  # Nothing is left waiting, so a second pass builds nothing.
  : >"$NIX_LOG"
  gotg port worker --once
  [ "$status" -eq 0 ]
  ! grep -q gotgWeb "$NIX_LOG"
}

@test "worker with nothing asked for does nothing" {
  gotg port worker --once
  [ "$status" -eq 0 ]
  ! grep -q gotgWeb "${NIX_LOG}" 2>/dev/null
}

@test "a failed build is reported to the web app with the reason" {
  ask_for_web_build
  NIX_FAIL_WEB=run gotg port worker --once
  [ "$status" -eq 0 ] # the worker carries on; the game failed
  [[ "$stderr" == *"the browser build of n64.usa.zelda failed"* ]]
  run web_api "$GOTG_SERVICE_URL/ports/n64.usa.zelda/web"
  [ "$(jq -r .request.state <<<"$output")" = failed ]
  [[ "$(jq -r .request.error <<<"$output")" == *"emcc: error: the compile went wrong"* ]]
  [ "$(jq -r .build <<<"$output")" = null ]
}

@test "a library with no builder for it fails the request" {
  ask_for_web_build
  NIX_FAIL_WEB=build gotg port worker --once
  run web_api "$GOTG_SERVICE_URL/ports/n64.usa.zelda/web"
  [ "$(jq -r .request.state <<<"$output")" = failed ]
  [[ "$(jq -r .request.error <<<"$output")" == *"has no browser build of n64.usa.zelda"* ]]
}
