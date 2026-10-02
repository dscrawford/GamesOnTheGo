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

@test "an already installed game is still known when the server is down" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg download usa.zelda
  stop_saves_service
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"

  gotg complete installed
  [ "$status" -eq 0 ]
  [ "$output" = "n64/usa.zelda" ]
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already installed"* ]]
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

@test "a game is installed once its bytes are here, and not before" {
  add_game n64 "usa.zelda.z64" "rom" "The Legend of Zelda"
  gotg refresh
  gotg complete installed
  [ "$status" -eq 0 ]
  [[ "$output" != *"usa.zelda"* ]]

  gotg download usa.zelda
  gotg complete installed
  [[ "$output" == *"n64/usa.zelda"* ]]
}

@test "the mods a game has variant environments for are its variants" {
  add_game gamecube "usa.super_mario_sunshine.rvz" "disc" "Super Mario Sunshine"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/gamecube"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.bse.nix"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.bsmso.nix"
  gotg refresh
  gotg complete variants usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  [ "$output" = $'bse\nbsmso' ]
}

@test "a game with no mods has no variants" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  gotg complete variants usa.zelda
  [ "$status" -eq 0 ]
  [ -z "$output" ]
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
  gotg download usa.evil
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid file name"* ]]
  [ ! -e "$TEST_TMP/escape.z64" ]
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

  # Everything cached is stale, so a lookup has to attempt a refresh — and
  # the refresh failing must degrade to the cache, not kill the command.
  # (The download itself then fails: the server is gone.)
  touch -d '2 days ago' "$GOTG_CACHE_FILE"
  gotg download usa.zelda
  [[ "$stderr" == *"using the cached catalog"* ]]
  [[ "$stderr" != *"no game called"* ]]
}

@test "no cache and no server is a plain failure, not a silent one" {
  stop_saves_service
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
  gotg download usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no catalog cached and none could be fetched"* ]]
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

  gotg download usa.zelda
  [[ "$stderr" == *"using the cached catalog"* ]]
  [[ "$stderr" != *"no game called"* ]]
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
  gotg download usa.zelda
  [[ "$stderr" != *"cached catalog"* ]]
  [[ "$stderr" != *"no game called"* ]]
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

@test "a poisoned platform is rejected before the mods glob" {
  mkdir -p "$GOTG_STATE_DIR"
  jq -n '{version: 2, games: [{id: "usa.evil", platform: "../../VICTIM",
          handler: "single_file", title: "Evil",
          files: [{name: "usa.evil.z64", size_bytes: 1, sha256: null}]}]}' \
    >"$GOTG_CACHE_FILE"
  gotg complete variants usa.evil
  [ -z "$output" ]
}

@test "a game's mods are its own — not the base env, not a sibling id's" {
  add_game gamecube "usa.mario.rvz" "disc" "Mario"
  add_game gamecube "usa.mario_kart.rvz" "disc" "Mario Kart"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/gamecube"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.mario.nix"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.mario.bse.nix"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.mario_kart.hd.nix"
  gotg refresh
  gotg complete variants usa.mario
  [ "$status" -eq 0 ]
  [ "$output" = "bse" ]
}

@test "only mods that play accepts are variants" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/n64"
  : >"$GOTG_ENV_DIR/games/n64/usa.zelda.bse.nix"
  : >"$GOTG_ENV_DIR/games/n64/usa.zelda.60.fps.nix"
  gotg refresh
  gotg complete variants usa.zelda
  [ "$status" -eq 0 ]
  [ "$output" = "bse" ]
}

# --- the catalog is the library's pin ------------------------------------------

@test "with a library, refresh is its pinned catalog: a root, no service round trip" {
  # Two catalogs -- the service's, and the one the library's lock names --
  # are two answers to "what is there", and the one a launch builds from is
  # the lock's. So that is the one the cache is, as a GC root to it.
  printf '{"version":2,"games":[{"platform":"n64","id":"usa.pinned","title":"From the pin","handler":"single_file","files":[{"name":"usa.pinned.z64","size":1,"sha256":"x"}]}]}' \
    >"$TEST_TMP/pinned.json"
  export LIBRARY_CATALOG="$TEST_TMP/pinned.json"
  use_library
  stop_saves_service
  gotg refresh
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"catalog updated: 1 game(s)"* ]]
  [ -L "$GOTG_CACHE_FILE" ]
  run jq -r '.games[0].title' "$GOTG_CACHE_FILE"
  [ "$output" = "From the pin" ]
  grep -qx "build $GOTG_LIBRARY#catalog -o $GOTG_CACHE_FILE" "$NIX_LOG"
}

@test "refresh moves the library's pin first, so what shows is what the server has now" {
  use_library
  gotg refresh
  [ "$status" -eq 0 ]
  [ "$(grep "catalog" "$NIX_LOG" | head -n1)" = "flake update catalog --flake $GOTG_LIBRARY" ]
  grep -qx "build $GOTG_LIBRARY#catalog -o $GOTG_CACHE_FILE" "$NIX_LOG"
}

@test "a pin that cannot move still gives the catalog it has" {
  # A library that is a store path, or a directory somebody else owns:
  # `flake update` fails, and the lock as it is is still a catalog.
  use_library
  export NIX_FAIL_FLAKE_UPDATE=1
  gotg refresh
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"could not update the library's catalog"* ]]
  [ -L "$GOTG_CACHE_FILE" ]
}

@test "behind a library a cache never goes stale: the pin is the truth until it is moved" {
  add_game n64 "usa.zelda.z64" "rom"
  use_library
  gotg refresh
  : >"$NIX_LOG"
  touch -d '3 days ago' "$GOTG_CACHE_FILE"
  gotg complete ready usa.zelda
  ! grep -q "catalog" "$NIX_LOG"
}

@test "a catalog from before the library was there is replaced, not written through" {
  # The old cache was a file; nix's -o over a file is an error, not a root.
  use_library
  mkdir -p "$GOTG_STATE_DIR"
  printf '{"version":2,"games":[]}' >"$GOTG_CACHE_FILE"
  gotg refresh
  [ "$status" -eq 0 ]
  [ -L "$GOTG_CACHE_FILE" ]
}
