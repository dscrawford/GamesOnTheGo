#!/usr/bin/env bats
# Adoption: the saves that existed before an emulator was told where to put them.
#
# Moving between machines is tested in saves-sync.bats. What is here is the part
# no sync can do for us, because it is about gotg having changed where an
# emulator writes: finding what the old location still holds, and copying it
# forward exactly once.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
  mkdir -p "$SERVER_ROOT/Games/.gotg"

  export GOTG_DEVICE_ID=aaaa1111
  export GOTG_NOW=2026-08-01T12:00:00Z

  # An environment whose saves are one directory, and a game that resolves to
  # it, so both ways of naming a target are exercised.
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64 '["saves/**"]' '["saves/*.o2r"]'

  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"

  STATE="$GOTG_ENV_STATE_DIR/env-n64"
  mkdir -p "$STATE/saves"
}

teardown() {
  stop_server
}

# An environment that also knows where its emulator used to keep saves, and
# files them the way ares really does: under a directory named for the console,
# which has a space in it on every platform ares supports.
fake_env_legacy() {
  local dir="$GOTG_ROOTS_DIR/env-n64/share/gotg"
  jq -n '{version: 1, name: "env-n64", saves: ["saves/**"], excludes: [],
          legacy: [{from: "$GAMES/n64/*.ram", into: "saves/Nintendo 64"}],
          saveStates: false}' \
    >"$dir/saves.json"
}

# Put a save in place, as an emulator would.
write_save() {
  mkdir -p "$STATE/saves"
  printf '%s' "${2:-contents}" >"$STATE/saves/$1"
}

@test "adopt says what it would do and changes nothing" {
  fake_env_legacy
  mkdir -p "$GOTG_GAMES_DIR/n64"
  printf 'old-save' >"$GOTG_GAMES_DIR/n64/usa.zelda.ram"

  gotg saves adopt --all
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"would copy"* ]]
  [[ "$stderr" == *"usa.zelda.ram"* ]]
  [[ "$stderr" == *"Nothing has been changed"* ]]
  [ ! -e "$STATE/saves/Nintendo 64/usa.zelda.ram" ]
}

@test "adopt copies, and leaves the originals where they were" {
  fake_env_legacy
  mkdir -p "$GOTG_GAMES_DIR/n64"
  printf 'old-save' >"$GOTG_GAMES_DIR/n64/usa.zelda.ram"

  gotg saves adopt --all --yes
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/Nintendo 64/usa.zelda.ram")" = "old-save" ]
  # If the mapping is wrong, the cost is disk rather than a save.
  [ "$(cat "$GOTG_GAMES_DIR/n64/usa.zelda.ram")" = "old-save" ]
}

@test "adopt never writes over a save that is already here" {
  fake_env_legacy
  mkdir -p "$GOTG_GAMES_DIR/n64"
  printf 'old-save' >"$GOTG_GAMES_DIR/n64/usa.zelda.ram"
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'newer-work' >"$STATE/saves/Nintendo 64/usa.zelda.ram"

  gotg saves adopt --all --yes
  [ "$status" -eq 0 ]
  # Anything already here came from a pull or from playing, and is the authority.
  [ "$(cat "$STATE/saves/Nintendo 64/usa.zelda.ram")" = "newer-work" ]
}

@test "the first launch adopts once, and only once" {
  fake_env_legacy
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  mkdir -p "$GOTG_GAMES_DIR/n64"
  printf 'old-save' >"$GOTG_GAMES_DIR/n64/usa.zelda.ram"

  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/Nintendo 64/usa.zelda.ram")" = "old-save" ]

  # Play on, then launch again: the older file must not come back over the top.
  printf 'played-since' >"$STATE/saves/Nintendo 64/usa.zelda.ram"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/Nintendo 64/usa.zelda.ram")" = "played-since" ]
}
