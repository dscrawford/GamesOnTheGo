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
  start_saves_service
  write_api_config
  load_client_libs
}

teardown() { stop_saves_service; }

# An environment that declares it needs keys, and the keys sitting on the
# server where the spec says they live.
fake_keys_env() {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  jq -n '{into: "config/Ryujinx/system", files: ["prod.keys", "title.keys"]}' \
    >"$GOTG_ROOTS_DIR/env-switch/share/gotg/keys.json"
}

serve_keys() {
  mkdir -p "$SERVICE_FILES_DIR/switch"
  printf 'master_key_00 = deadbeef\n' >"$SERVICE_FILES_DIR/switch/prod.keys"
  printf 'title_key_00 = cafe\n' >"$SERVICE_FILES_DIR/switch/title.keys"
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
  # With the service stopped, a fetch would fail; nothing should be attempted.
  stop_saves_service
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -s "$(keys_dir)/prod.keys" ]
  start_saves_service
}

@test "a missing key warns and still lets the launch happen" {
  fake_keys_env
  mkdir -p "$SERVICE_FILES_DIR/switch"
  printf 'master_key_00 = deadbeef\n' >"$SERVICE_FILES_DIR/switch/prod.keys"
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
  stop_saves_service
  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  start_saves_service
}

@test "an environment that needs no keys does nothing at all" {
  mkdir -p "$GOTG_ROOTS_DIR/env-snes/share/gotg"
  run keys_ensure env-snes snes
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# --- somebody else's platform ------------------------------------------------

@test "a manifest may take a file from another platform's directory" {
  # Four Swords Adventures is a GameCube game that will not emulate its Game
  # Boy Advances without the GBA's own BIOS, and there is one copy of that,
  # under gba — not one per platform that turns out to want it.
  mkdir -p "$GOTG_ROOTS_DIR/env-gamecube-fsa/share/gotg"
  jq -n '{platform: "gba", into: "bios", files: ["bios.zip"]}' \
    >"$GOTG_ROOTS_DIR/env-gamecube-fsa/share/gotg/keys.json"
  mkdir -p "$SERVICE_FILES_DIR/gba"
  printf 'PK\003\004 not really a zip\n' >"$SERVICE_FILES_DIR/gba/bios.zip"

  run keys_ensure env-gamecube-fsa gamecube
  [ "$status" -eq 0 ]
  [ -s "$GOTG_STATE_DIR/env/env-gamecube-fsa/bios/bios.zip" ]
}

@test "the byte host serves the keys, not the control plane" {
  # /files lives beside /games, off the proxied control plane — a deployment
  # that splits them answers for keys on the host the catalog names, and the
  # control plane 503s for a service that is working perfectly.
  fake_keys_env
  serve_keys
  # A catalog naming a byte host, and a control plane that has no files at all.
  gotg refresh
  jq '. + {files_url: $url}' --arg url "$GOTG_SERVICE_URL" "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"

  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -s "$(keys_dir)/prod.keys" ]
}

@test "a catalog naming a byte host that has moved is re-read, not given up on" {
  # The real failure: the files host sits behind a VPN whose forwarded port is
  # reassigned on reconnect, so a cache from before one names a port nothing
  # answers on. Without the re-read, a reconnect is a console that cannot
  # decrypt a game until somebody thinks to run `gotg refresh`.
  fake_keys_env
  serve_keys
  gotg refresh
  jq '. + {files_url: "http://127.0.0.1:1"}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"

  run keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -s "$(keys_dir)/prod.keys" ]
}

@test "and a host that is simply down is a warning, not an endless retry" {
  fake_keys_env
  # Nothing served, so every host fails on both passes.
  gotg refresh
  jq '. + {files_url: "http://127.0.0.1:1"}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"

  run --separate-stderr keys_ensure env-switch switch
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no prod.keys"* ]]
}
