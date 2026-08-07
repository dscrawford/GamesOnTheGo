#!/usr/bin/env bats
# Console keys — the files an emulator needs that are not the game.
#
# A Switch game will not decrypt without them, so the thing worth getting right
# is that they arrive before the emulator starts, and that a launch without them
# still happens: the emulator's complaint about a specific game is more use than
# ours about a file.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
  load_client_libs
}

teardown() { stop_server; }

# An environment that declares it needs keys, and the keys sitting on the
# server where the spec says they live.
fake_keys_env() {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  jq -n '{into: "config/Ryujinx/system", files: ["prod.keys", "title.keys"]}' \
    >"$GOTG_ROOTS_DIR/env-switch/share/gotg/keys.json"
}

serve_keys() {
  mkdir -p "$SERVER_ROOT/Games/switch"
  printf 'master_key_00 = deadbeef\n' >"$SERVER_ROOT/Games/switch/prod.keys"
  printf 'title_key_00 = cafe\n' >"$SERVER_ROOT/Games/switch/title.keys"
}

keys_dir() { printf '%s/env-switch/config/Ryujinx/system' "$GOTG_STATE_DIR/env"; }

@test "keys are fetched into the directory the emulator reads" {
  fake_keys_env
  serve_keys
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -s "$(keys_dir)/prod.keys" ]
  [ -s "$(keys_dir)/title.keys" ]
  grep -q "master_key_00" "$(keys_dir)/prod.keys"
}

@test "they land 0600 — a key is not world-readable" {
  fake_keys_env
  serve_keys
  keys_ensure env-switch switch
  [ "$(stat -c '%a' "$(keys_dir)/prod.keys")" = "600" ]
}

@test "a second launch does not fetch again" {
  fake_keys_env
  serve_keys
  keys_ensure env-switch switch
  # With the server stopped, a fetch would fail; nothing should be attempted.
  stop_server
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -s "$(keys_dir)/prod.keys" ]
  start_server
}

@test "a missing key warns and still lets the launch happen" {
  fake_keys_env
  mkdir -p "$SERVER_ROOT/Games/switch"
  printf 'master_key_00 = deadbeef\n' >"$SERVER_ROOT/Games/switch/prod.keys"
  # title.keys deliberately absent: not every dump needs it.
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -s "$(keys_dir)/prod.keys" ]
  [[ "$output" == *"no title.keys"* ]]
  [[ "$output" == *"IMPORTER_SPEC"* ]]
}

@test "an interrupted fetch leaves no half a key behind" {
  fake_keys_env
  # Nothing on the server at all.
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ ! -e "$(keys_dir)/prod.keys" ]
  [ ! -e "$(keys_dir)/prod.keys.part" ]
}

@test "an unreachable server is a warning, not a failed launch" {
  fake_keys_env
  stop_server
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  start_server
}

@test "an environment that needs no keys does nothing at all" {
  mkdir -p "$GOTG_ROOTS_DIR/env-snes/share/gotg"
  run keys_ensure env-snes snes
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
