#!/usr/bin/env bats
# The catalog, credentials and id resolution.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
}

teardown() {
  stop_saves_service
}

@test "refresh caches the catalog locally" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  [ "$status" -eq 0 ]
  [ -s "$GOTG_CACHE_FILE" ]
  run jq -r '.games[0].platform' "$GOTG_CACHE_FILE"
  [ "$output" = "n64" ]
}

@test "an already installed game still lists when the server is down" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  stop_saves_service
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"

  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
}

@test "an id on two platforms is reported as ambiguous, not guessed" {
  # This really happens: the same title is in both the N64 and SNES sets.
  add_game n64 "usa.bugs_life.zip" "n64 version"
  add_game snes "usa.bugs_life.zip" "snes version"
  gotg refresh

  gotg download usa.bugs_life
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"ambiguous"* ]]
  [[ "$stderr" == *"n64/usa.bugs_life"* ]]
  [[ "$stderr" == *"snes/usa.bugs_life"* ]]
}

@test "qualifying an ambiguous id picks the right platform" {
  add_game n64 "usa.bugs_life.zip" "n64 version"
  add_game snes "usa.bugs_life.zip" "snes version"
  gotg refresh

  gotg download snes/usa.bugs_life
  [ "$status" -eq 0 ]
  run cat "$GOTG_GAMES_DIR/snes/usa.bugs_life.zip"
  [ "$output" = "snes version" ]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.bugs_life.zip" ]
}

@test "an unknown id fails with a usable message" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg download usa.not_a_game
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no game called"* ]]
}

@test "a malformed id is rejected before it reaches a path" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg download "../../etc/passwd"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid game id"* ]]
}

@test "a world-readable token file is refused" {
  add_game n64 "usa.zelda.z64" "rom"
  chmod 644 "$GOTG_CONFIG_DIR/api.json"
  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"chmod 600"* ]]
}

@test "a wrong token produces a clear message" {
  add_game n64 "usa.zelda.z64" "rom"
  jq '.token = "wrong"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"could not fetch the catalog"* ]]
}

@test "an empty catalog is not an error, only empty" {
  gotg refresh
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"0 game(s)"* ]]
}

@test "info reports install state" {
  add_game n64 "usa.zelda.z64" "rom" "The Legend of Zelda"
  gotg refresh
  gotg info usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed: no"* ]]
  [[ "$output" == *"The Legend of Zelda"* ]]

  gotg download usa.zelda
  gotg info usa.zelda
  [[ "$output" == *"installed: yes"* ]]
}

@test "info lists the mods a game has variant environments for" {
  add_game gamecube "usa.super_mario_sunshine.rvz" "disc" "Super Mario Sunshine"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/gamecube"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.bse.nix"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.bsmso.nix"
  gotg refresh
  gotg info usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  [[ "$output" == *"mods:"*"bse, bsmso"* ]]
}

@test "info stays silent about mods when a game has none" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  gotg info usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" != *"mods:"* ]]
}

@test "names with spaces and punctuation survive the round trip" {
  mkdir -p "$SERVICE_LIBRARY_DIR/snes"
  printf 'rom' >"$SERVICE_LIBRARY_DIR/snes/Zelda's Quest (USA) [!].sfc"
  local files
  files="$(jq -n --arg p "$SERVICE_LIBRARY_DIR/snes/Zelda's Quest (USA) [!].sfc" \
    '[{name: "Zelda'"'"'s Quest (USA) [!].sfc", path: $p, size_bytes: 3, mtime: 1, sha256: null}]')"
  add_member_game snes usa.zeldas_quest "Zelda's Quest" single_file "$files"
  gotg refresh
  gotg download usa.zeldas_quest
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/snes/Zelda's Quest (USA) [!].sfc" ]
}

@test "a poisoned cache cannot escape the games directory via its platform" {
  mkdir -p "$GOTG_STATE_DIR" "$TEST_TMP/VICTIM"
  printf 'precious' >"$TEST_TMP/VICTIM/important.txt"
  jq -n '{version: 2, games: [{id: "usa.evil", platform: "../../VICTIM",
          handler: "single_file", title: "Evil",
          files: [{name: "usa.evil.z64", size_bytes: 1, sha256: null}]}]}' \
    >"$GOTG_CACHE_FILE"
  gotg download usa.evil
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid platform"* ]]
  [ -f "$TEST_TMP/VICTIM/important.txt" ]
}

@test "a poisoned cache with a hostile member name is rejected" {
  mkdir -p "$GOTG_STATE_DIR"
  jq -n '{version: 2, games: [{id: "usa.evil", platform: "n64",
          handler: "single_file", title: "Evil",
          files: [{name: "../../escape.z64", size_bytes: 1, sha256: null}]}]}' \
    >"$GOTG_CACHE_FILE"
  gotg info usa.evil
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid file name"* ]]
}

@test "a stale cache survives an unreachable server — the offline fallback" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  stop_saves_service
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"

  # Everything cached is stale, so list has to attempt a refresh — and the
  # refresh failing must degrade to the cache, not kill the command.
  touch -d '2 days ago' "$GOTG_CACHE_FILE"
  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
  [[ "$stderr" == *"using the cached catalog"* ]]
}

@test "no cache and no server is a plain failure, not a silent one" {
  stop_saves_service
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
  gotg list
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"catalog"* ]]
}

@test "an explicit refresh against a dead server fails loudly" {
  stop_saves_service
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
  gotg refresh
  [ "$status" -ne 0 ]
}

@test "a traversal, a leading dot or a control character is invalid" {
  load_client_libs
  run validate_filename "usa.zelda.z64"
  [ "$status" -eq 0 ]
  run validate_filename "Legend of Zelda, The (USA).z64"
  [ "$status" -eq 0 ]
  run validate_filename "code/app.rpx"
  [ "$status" -eq 0 ]
  local bad
  for bad in "a/../b.z64" "a//b" ".." "." ".hidden" "" "$(printf 'a\tb')" "a/b/c/d/e/f/g/h/i"; do
    run -1 --separate-stderr validate_filename "$bad"
  done
}

@test "an unreachable service over a stale cache falls back, cache intact" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  local before
  before="$(cat "$GOTG_CACHE_FILE")"
  stop_saves_service
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
  touch -d '2 days ago' "$GOTG_CACHE_FILE"

  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
  [[ "$stderr" == *"using the cached catalog"* ]]
  [ "$(cat "$GOTG_CACHE_FILE")" = "$before" ]
  start_saves_service
}


@test "a cache with a future mtime is fresh, not endlessly re-fetched" {
  # Clock skew is real: NFS, a resumed laptop. A negative age must read as
  # fresh rather than tripping an arithmetic surprise.
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  stop_saves_service
  touch -d '1 hour hence' "$GOTG_CACHE_FILE"
  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
}

@test "segment length caps at 255" {
  load_client_libs
  run validate_filename "$(printf 'a%.0s' {1..255})"
  [ "$status" -eq 0 ]
  run -1 --separate-stderr validate_filename "$(printf 'a%.0s' {1..256})"
}

@test "a unicode filename is valid regardless of locale" {
  # The server accepts this name unconditionally; the client must not
  # disagree just because the test sandbox runs under LC_ALL=C.
  load_client_libs
  run validate_filename "ゼルダの伝説.z64"
  [ "$status" -eq 0 ]
}
