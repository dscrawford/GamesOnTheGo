#!/usr/bin/env bats
# The catalog, credentials and id resolution.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
}

teardown() {
  stop_server
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
  stop_server

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

@test "a world-readable config is refused" {
  add_game n64 "usa.zelda.z64" "rom"
  chmod 644 "$GOTG_CONFIG_FILE"
  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"chmod 600"* ]]
}

@test "wrong credentials produce a clear message" {
  add_game n64 "usa.zelda.z64" "rom"
  jq '.password = "wrong"' "$GOTG_CONFIG_FILE" >"$GOTG_CONFIG_FILE.tmp"
  mv "$GOTG_CONFIG_FILE.tmp" "$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"

  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"username and password"* ]]
}

@test "a missing catalog explains that the importer has not run" {
  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"importer"* ]]
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

@test "names with spaces and punctuation survive the round trip" {
  # Real filenames are full of these; they must be encoded per path segment.
  mkdir -p "$SERVER_ROOT/Games/n64"
  local name="usa.game (USA) & friends.z64"
  printf 'rom' >"$SERVER_ROOT/Games/n64/$name"
  add_manifest_entry n64 "/Games/n64/$name" file 3 "" "Punctuated"

  gotg refresh
  # The id derives from the filename, so check it resolves and downloads.
  run jq -r '.games[0].path' "$GOTG_CACHE_FILE"
  [[ "$output" == *"& friends"* ]]
}
