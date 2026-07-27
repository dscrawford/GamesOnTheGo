#!/usr/bin/env bats
# Downloading: the path a Steam launch takes when a game is not here yet.

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

@test "list marks which games are already installed" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  gotg refresh
  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"[ ]"*"usa.zelda"* ]]

  gotg download usa.zelda
  [ "$status" -eq 0 ]
  gotg list
  [[ "$output" == *"[*]"*"usa.zelda"* ]]
}

@test "a downloaded game matches the server byte for byte" {
  add_game n64 "usa.zelda.z64" "the actual rom bytes"
  gotg refresh
  gotg download usa.zelda

  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
  run diff "$SERVER_ROOT/Games/n64/usa.zelda.z64" "$GOTG_GAMES_DIR/n64/usa.zelda.z64"
  [ "$status" -eq 0 ]
}

@test "a corrupted download is rejected and deleted, not installed" {
  add_game n64 "usa.zelda.z64" "rom-content"
  # Publish a checksum that cannot match, as a truncated transfer would produce.
  jq '.games[0].sha256 = "0000000000000000000000000000000000000000000000000000000000000000"' \
    "$SERVER_ROOT/Games/.gotg/manifest.json" >"$SERVER_ROOT/Games/.gotg/m.tmp"
  mv "$SERVER_ROOT/Games/.gotg/m.tmp" "$SERVER_ROOT/Games/.gotg/manifest.json"

  gotg refresh
  gotg download usa.zelda

  [ "$status" -ne 0 ]
  [[ "$stderr" == *"checksum mismatch"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
  run find "$GOTG_PARTIAL_DIR" -type f
  [ -z "$output" ]
}

@test "an interrupted download resumes instead of starting over" {
  local payload
  payload="$(head -c 200000 /dev/urandom | base64 | head -c 100000)"
  add_game n64 "usa.big.z64" "$payload"
  gotg refresh

  # First attempt: the server hangs up part way through.
  stop_server
  start_server --fail-after 20000
  write_config
  gotg download usa.big
  [ "$status" -ne 0 ]

  # The partial file is kept, and holds only what arrived.
  local partial="$GOTG_PARTIAL_DIR/usa.big"
  [ -f "$partial" ]
  local partial_size
  partial_size="$(stat -c '%s' "$partial")"
  [ "$partial_size" -gt 0 ]
  [ "$partial_size" -lt "${#payload}" ]

  # Second attempt against a healthy server completes it.
  stop_server
  start_server
  write_config
  gotg download usa.big
  [ "$status" -eq 0 ]
  run diff "$SERVER_ROOT/Games/n64/usa.big.z64" "$GOTG_GAMES_DIR/n64/usa.big.z64"
  [ "$status" -eq 0 ]
}

@test "a directory game arrives unzipped with its layout intact" {
  mkdir -p "$SERVER_ROOT/Games/wiiu/usa.title/code" "$SERVER_ROOT/Games/wiiu/usa.title/meta"
  echo rpx >"$SERVER_ROOT/Games/wiiu/usa.title/code/game.rpx"
  echo xml >"$SERVER_ROOT/Games/wiiu/usa.title/meta/meta.xml"
  add_manifest_entry wiiu "/Games/wiiu/usa.title" dir 100 "" "A Wii U Game"

  gotg refresh
  gotg download usa.title

  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/wiiu/usa.title/code/game.rpx" ]
  [ -f "$GOTG_GAMES_DIR/wiiu/usa.title/meta/meta.xml" ]
}

@test "downloading twice does not re-fetch" {
  add_game n64 "usa.zelda.z64" "rom-content"
  gotg refresh
  gotg download usa.zelda
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already installed"* ]]
}

@test "a partial download never looks like an installed game" {
  add_game n64 "usa.zelda.z64" "$(head -c 60000 /dev/zero | tr '\0' 'x')"
  gotg refresh
  stop_server
  start_server --fail-after 5000
  write_config

  gotg download usa.zelda
  [ "$status" -ne 0 ]
  # Staging lives outside the platform directory, so nothing half-written is
  # ever visible where a launcher would look for it.
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}
