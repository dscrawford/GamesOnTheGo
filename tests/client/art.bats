#!/usr/bin/env bats
# Warming the service's art cache: `gotg admin art warm`.
#
# The trade this makes is the reason it exists. One run resolves the whole
# catalog slowly, against the upstreams, and writes every answer — including
# every "nobody has this" — to the service. After that a grid on any machine
# is a download from us, and SteamGridDB hears from nobody.
#
# Which puts the weight of these tests on the misses. A miss is permanent and
# fleet-wide: written when an upstream really said no, it saves thousands of
# pointless questions, and written because the network hiccuped it blanks a
# tile for everybody, forever.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  start_sgdb
  start_libretro
  export GOTG_STEAMGRIDDB_URL="$SGDB_URL"
  export GOTG_LIBRETRO_URL="$LIBRETRO_URL"
  export GOTG_INDEX_TOKEN="index-token"
}

teardown() {
  stop_sgdb
  stop_libretro
  stop_saves_service
}

start_sgdb() {
  SGDB_PORT="$(pick_port)"
  python3 "$BATS_TEST_DIRNAME/mock_steamgriddb.py" "$SGDB_PORT" "$@" &
  SGDB_PID=$!
  export SGDB_URL="http://127.0.0.1:$SGDB_PORT"
  local i
  for i in $(seq 1 50); do
    curl -s -o /dev/null "$SGDB_URL/img/probe" 2>/dev/null && return 0
    sleep 0.1
  done
  echo "mock steamgriddb did not start" >&2
  return 1
}

stop_sgdb() { [[ -n "${SGDB_PID:-}" ]] && kill "$SGDB_PID" 2>/dev/null || true; }

start_libretro() {
  LIBRETRO_PORT="$(pick_port)"
  python3 "$BATS_TEST_DIRNAME/mock_libretro.py" "$LIBRETRO_PORT" "$@" &
  LIBRETRO_PID=$!
  export LIBRETRO_URL="http://127.0.0.1:$LIBRETRO_PORT"
  local i
  for i in $(seq 1 50); do
    curl -s -o /dev/null "$LIBRETRO_URL/" 2>/dev/null && return 0
    sleep 0.1
  done
  echo "mock libretro did not start" >&2
  return 1
}

stop_libretro() { [[ -n "${LIBRETRO_PID:-}" ]] && kill "$LIBRETRO_PID" 2>/dev/null || true; }

restart_sgdb() {
  stop_sgdb
  start_sgdb "$@"
  export GOTG_STEAMGRIDDB_URL="$SGDB_URL"
}

restart_libretro() {
  stop_libretro
  start_libretro "$@"
  export GOTG_LIBRETRO_URL="$LIBRETRO_URL"
}

# Reading art back the way a client's grid does: the ordinary api.json token.
client_art() {
  curl -sS -o "$2" -w '%{http_code}' -H "Authorization: Bearer test-token" \
    "$GOTG_SERVICE_URL/art/$1"
}

warm() { run "$GOTG_BIN" admin art warm --rate 0 "$@"; }

@test "a warm run fills the service with a picture the grid can download" {
  add_game n64 usa.some_game.z64 "rom" "Some Game"
  warm
  [ "$status" -eq 0 ]
  [ "$(jq -r '.art' <<<"$output")" = "1" ]

  code="$(client_art n64/usa.some_game "$TEST_TMP/tile")"
  [ "$code" = "200" ]
  # A real picture, not an error page: the service only stores what has an
  # image's magic bytes.
  run python3 -c "import sys; sys.exit(0 if open(sys.argv[1],'rb').read(8) == b'\x89PNG\r\n\x1a\n' else 1)" "$TEST_TMP/tile"
  [ "$status" -eq 0 ]
}

@test "a game nothing has art for is written down as a miss" {
  restart_sgdb --no-match
  restart_libretro --empty
  add_game n64 usa.obscure_game.z64 "rom" "Obscure Game"
  warm
  [ "$status" -eq 0 ]
  [ "$(jq -r '.misses' <<<"$output")" = "1" ]

  code="$(client_art n64/usa.obscure_game "$TEST_TMP/tile")"
  [ "$code" = "404" ]
  # "miss" and "absent" are different answers: one stops a client asking, the
  # other only means nobody has looked yet.
  run curl -sS -D - -o /dev/null -H "Authorization: Bearer test-token" \
    "$GOTG_SERVICE_URL/art/n64/usa.obscure_game"
  [[ "$output" == *"X-Gotg-Art: miss"* ]]
}

@test "an upstream that could not be reached is never recorded as a miss" {
  # The failure that would poison the cache for the whole fleet.
  stop_sgdb
  export GOTG_STEAMGRIDDB_URL="http://127.0.0.1:$(pick_port)"
  restart_libretro --empty
  add_game n64 usa.unlucky_game.z64 "rom" "Unlucky Game"
  warm
  [ "$status" -eq 0 ]
  [ "$(jq -r '.deferred' <<<"$output")" = "1" ]
  [ "$(jq -r '.misses' <<<"$output")" = "0" ]

  run curl -sS -D - -o /dev/null -H "Authorization: Bearer test-token" \
    "$GOTG_SERVICE_URL/art/n64/usa.unlucky_game"
  [[ "$output" == *"X-Gotg-Art: absent"* ]]
}

@test "a second run asks about nothing it already knows" {
  add_game n64 usa.some_game.z64 "rom" "Some Game"
  warm
  [ "$(jq -r '.art' <<<"$output")" = "1" ]
  warm
  [ "$(jq -r '.known' <<<"$output")" = "1" ]
  [ "$(jq -r '.art' <<<"$output")" = "0" ]
}

@test "--limit turns a long warm into a short one" {
  add_game n64 usa.game_one.z64 "rom" "Game One"
  add_game n64 usa.game_two.z64 "rom" "Game Two"
  warm --limit 1
  [ "$(jq -r '.art' <<<"$output")" = "1" ]
}

@test "--dry-run asks nobody anything" {
  add_game n64 usa.some_game.z64 "rom" "Some Game"
  warm --dry-run
  [ "$status" -eq 0 ]
  code="$(client_art n64/usa.some_game "$TEST_TMP/tile")"
  [ "$code" = "404" ]
}

@test "warming needs the index token, not a client's" {
  add_game n64 usa.some_game.z64 "rom" "Some Game"
  GOTG_INDEX_TOKEN="" run "$GOTG_BIN" admin art warm --rate 0
  [ "$status" -ne 0 ]
  [[ "$output" == *"GOTG_INDEX_TOKEN"* ]]
}

@test "a client token cannot put a picture in the cache" {
  printf '\x89PNG\r\n\x1a\n' >"$TEST_TMP/pic.png"
  run curl -sS -o /dev/null -w '%{http_code}' -X PUT \
    -H "Authorization: Bearer test-token" --data-binary @"$TEST_TMP/pic.png" \
    "$GOTG_SERVICE_URL/art/n64/usa.some_game"
  [ "$output" = "403" ]
}

@test "status counts what the cache holds" {
  add_game n64 usa.some_game.z64 "rom" "Some Game"
  warm
  run "$GOTG_BIN" admin art status
  [ "$status" -eq 0 ]
  [[ "$output" == *"art:    1"* ]]
}
