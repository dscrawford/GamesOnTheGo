#!/usr/bin/env bats
# Save sync, on Ludusavi.
#
# Ludusavi does the parts that are the same for everyone — walking the files,
# noticing what changed, keeping old copies, and driving rclone — and gotg keeps
# the parts that are ours: which environment a game belongs to, where that
# environment keeps its saves, what counts as a save there, and refusing to move
# something that is obviously not one.
#
# These run against the real ludusavi and the real rclone, with an rclone
# `alias` remote pointing at a directory. That is a whole cloud round trip with
# no server in it, so what is tested is the actual tool rather than a mock of
# what it was assumed to do.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config

  export GOTG_DEVICE_ID=aaaa1111
  export GOTG_NOW=2026-08-01T12:00:00Z
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"

  # The remote every machine in a test shares: one directory, reached through
  # rclone exactly as a WebDAV server would be.
  export REMOTE_DIR="$TEST_TMP/remote"
  mkdir -p "$REMOTE_DIR"

  fake_env env-n64 '["saves/**"]' '["saves/*.o2r"]'
  use_local_remote
  STATE="$GOTG_ENV_STATE_DIR/env-n64"
  mkdir -p "$STATE/saves"
}

teardown() { stop_server; }

# A remote that is a directory. `alias` is a real rclone backend, so this
# exercises the same code path as WebDAV up to the transport itself.
use_local_remote() {
  mkdir -p "$GOTG_CONFIG_DIR"
  chmod 700 "$GOTG_CONFIG_DIR"
  jq -n --arg path "$REMOTE_DIR" '{type: "alias", path: $path}' \
    >"$GOTG_CONFIG_DIR/saves-remote.json"
}

write_save() {
  mkdir -p "$STATE/saves"
  printf '%s' "${2:-contents}" >"$STATE/saves/$1"
}

# Everything a second machine is: its own state and config, the same remote.
second_device() {
  export GOTG_STATE_DIR="$TEST_TMP/state-b"
  export GOTG_CONFIG_DIR="$TEST_TMP/config-b"
  export GOTG_CONFIG_FILE="$GOTG_CONFIG_DIR/config.json"
  export GOTG_CACHE_FILE="$GOTG_STATE_DIR/manifest.json"
  export GOTG_ROOTS_DIR="$GOTG_STATE_DIR/roots"
  export GOTG_ENV_STATE_DIR="$GOTG_STATE_DIR/env"
  export GOTG_SAVES_DIR="$GOTG_STATE_DIR/saves"
  export GOTG_DEVICE_ID=bbbb2222
  write_config
  fake_env env-n64 '["saves/**"]' '["saves/*.o2r"]'
  use_local_remote
  STATE="$GOTG_ENV_STATE_DIR/env-n64"
  mkdir -p "$STATE/saves"
}

first_device() {
  export GOTG_STATE_DIR="$TEST_TMP/state"
  export GOTG_CONFIG_DIR="$TEST_TMP/config"
  export GOTG_CONFIG_FILE="$GOTG_CONFIG_DIR/config.json"
  export GOTG_CACHE_FILE="$GOTG_STATE_DIR/manifest.json"
  export GOTG_ROOTS_DIR="$GOTG_STATE_DIR/roots"
  export GOTG_ENV_STATE_DIR="$GOTG_STATE_DIR/env"
  export GOTG_SAVES_DIR="$GOTG_STATE_DIR/saves"
  export GOTG_DEVICE_ID=aaaa1111
  STATE="$GOTG_ENV_STATE_DIR/env-n64"
}

lud_config() { cat "$GOTG_STATE_DIR/ludusavi/ludusavi/config.yaml"; }

# --- the config gotg generates ---------------------------------------------

@test "each built environment is a custom game named for itself" {
  write_save zelda.ram
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  lud_config | grep -q 'name: env-n64'
}

@test "the manifest's globs become that game's files, under its state dir" {
  write_save zelda.ram
  gotg saves status env-n64
  # Translated, not passed through: `saves/**` means "everything under saves"
  # to bash and to tar, but to Ludusavi `**` is "at least one directory down",
  # which would silently skip every memory save sitting directly in saves/.
  #
  # Matched at the end of a line, because gotg writes this file as JSON — YAML
  # being a superset of it — and Ludusavi rewrites it as ordinary YAML the first
  # time it runs, so either form has to satisfy this.
  lud_config | grep -q -- "$STATE/saves\"\?$"
  run ! grep -qF "$STATE/saves/**" "$GOTG_STATE_DIR/ludusavi/ludusavi/config.yaml"
}

@test "a glob that is not a whole directory is left as it is" {
  fake_env env-gc '["saves/*.gci", "nand/**"]' '[]'
  mkdir -p "$GOTG_ENV_STATE_DIR/env-gc/saves"
  printf 'x' >"$GOTG_ENV_STATE_DIR/env-gc/saves/mario.gci"
  gotg saves status env-gc
  lud_config | grep -qF "$GOTG_ENV_STATE_DIR/env-gc/saves/*.gci"
}

@test "the manifest's excludes become ignored paths" {
  write_save zelda.ram
  gotg saves status env-n64
  lud_config | grep -qF "$STATE/saves/*.o2r"
}

@test "it writes its own config and never the user's" {
  write_save zelda.ram
  local mine="$TEST_TMP/home/.config/ludusavi/config.yaml"
  mkdir -p "$(dirname "$mine")"
  printf 'mine, untouched\n' >"$mine"
  gotg saves push env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$mine")" = "mine, untouched" ]
}

# --- the round trip ---------------------------------------------------------

@test "a save pushed on one machine arrives on another" {
  write_save zelda.ram "hyrule"
  gotg saves push env-n64
  [ "$status" -eq 0 ]

  second_device
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "hyrule" ]
}

@test "a push with nothing changed since the last one sends nothing" {
  write_save zelda.ram
  gotg saves push env-n64
  gotg saves push env-n64
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"up to date"* ]]
}

@test "a pull keeps a local save the remote has never heard of" {
  write_save zelda.ram "hyrule"
  gotg saves push env-n64

  second_device
  write_save local_only.ram "mine"
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/local_only.ram")" = "mine" ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "hyrule" ]
}

@test "a pull archives what was here before overwriting it" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  write_save zelda.ram "from machine b"
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine a" ]

  # The overwritten save is still recoverable, which is the whole promise. It is
  # archived in gotg's own directory rather than Ludusavi's, because the next
  # cloud download mirrors the remote over Ludusavi's and would take it along.
  local archive
  archive="$(find "$GOTG_SAVES_DIR/local/env-n64" -name '*.tar.zst' | head -1)"
  [ -n "$archive" ]
  tar -xOf "$archive" saves/zelda.ram | grep -q "from machine b"
}

@test "status says when this machine has work the remote has not seen" {
  write_save zelda.ram
  gotg saves push env-n64
  write_save zelda.ram "played some more"
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"changed since the last sync"* ]]
}

@test "status works with no remote configured at all" {
  rm -f "$GOTG_CONFIG_DIR/saves-remote.json"
  write_save zelda.ram
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64"* ]]
}

@test "a save filed under a console name with a space survives the trip" {
  # ares files saves under a directory named for the console, and every one of
  # those names has a space in it. The path crosses JSON, YAML, an rclone
  # config and an argv on the way, so this is the case that breaks first.
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'ocarina' >"$STATE/saves/Nintendo 64/zelda.ram"
  gotg saves push env-n64
  [ "$status" -eq 0 ]

  second_device
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/Nintendo 64/zelda.ram")" = "ocarina" ]
}

@test "two machines editing the same save is refused, not merged" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  write_save zelda.ram "from machine b"
  gotg saves push env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"has saves this machine has not taken yet"* ]]

  # Neither side lost anything: machine b still has its own work.
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine b" ]
}

@test "forcing a push replaces the remote and says nothing was lost" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  write_save zelda.ram "from machine b"
  gotg saves push --force env-n64
  [ "$status" -eq 0 ]

  first_device
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine b" ]
}

# --- the guards that are ours ----------------------------------------------

@test "it refuses to push something far too big to be a save" {
  write_save zelda.ram
  head -c 200000 /dev/zero >"$STATE/saves/huge.ram"
  GOTG_SAVES_MAX_BYTES=100000 gotg saves push env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"huge.ram"* ]]
  # Refused before anything was uploaded.
  [ -z "$(ls -A "$REMOTE_DIR")" ]
}

@test "a backup that would restore outside the environment is refused" {
  write_save zelda.ram "hyrule"
  gotg saves push env-n64

  second_device
  # What a server that had been tampered with would send: a mapping that puts
  # the file somewhere that is not a save directory at all.
  local mapping
  mapping="$(find "$REMOTE_DIR" -name mapping.yaml | head -1)"
  [ -n "$mapping" ]
  sed -i "s|$TEST_TMP/state/env/env-n64|$TEST_TMP/pwned|g" "$mapping"

  gotg saves pull env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"outside"* ]]
  [ ! -e "$TEST_TMP/pwned" ]
}

@test "an environment that is not built here is not invented" {
  gotg saves push env-gamecube
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not built here"* ]]
}

@test "a game id is resolved to the environment its saves belong to" {
  add_game n64 usa.zelda.z64 "rom" "Zelda"
  write_save zelda.ram
  gotg saves status usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"env-n64"* ]]
}
