#!/usr/bin/env bats
# Save sync: bundling, generations, and what happens when two machines disagree.
#
# A second machine is a second state and config directory pointed at the same
# mock server, which is what divergence actually is.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
  mkdir -p "$SERVER_ROOT/Games/.gotg"

  export GOTG_SAVES_BACKEND=filebrowser
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

# Everything a second machine is: its own state, config and journal, same server.
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

@test "setup proves the backend works instead of claiming it does" {
  gotg saves setup
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"upload and download: ok"* ]]
  # Delete is checked too, because tidying old generations will need it.
  [[ "$stderr" == *"delete: ok"* ]]
  # The probe cleans up after itself.
  [ ! -e "$SERVER_ROOT/Games/.gotg/saves/env-probe/latest.json" ]
}

@test "setup keeps the credentials login wrote" {
  gotg saves setup
  [ "$status" -eq 0 ]
  run jq -r '.server, .username, .password, .saves_backend' "$GOTG_CONFIG_FILE"
  [ "${lines[0]}" = "$GOTG_SERVER_URL" ]
  [ "${lines[1]}" = "tester" ]
  [ "${lines[2]}" = "hunter2" ]
  [ "${lines[3]}" = "filebrowser" ]
}

@test "a push and a pull on another machine carry the save across" {
  write_save "zelda.ram" "save-data-one"
  gotg saves push --all
  [ "$status" -eq 0 ]

  second_device
  [ ! -e "$STATE/saves/zelda.ram" ]
  gotg saves pull --all
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "save-data-one" ]
}

@test "a save filed under a console name with a space in it survives the trip" {
  # Not a corner case: ares files every save under a directory named for the
  # console, so "saves/Super Famicom/x.ram" is the ordinary shape.
  mkdir -p "$STATE/saves/Super Famicom"
  printf 'nested-save' >"$STATE/saves/Super Famicom/usa.super_mario_world.ram"
  gotg saves push --all
  [ "$status" -eq 0 ]

  second_device
  gotg saves pull --all
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/Super Famicom/usa.super_mario_world.ram")" = "nested-save" ]
}

@test "an unchanged save set uploads nothing the second time" {
  write_save "zelda.ram"
  gotg saves push --all
  [ "$status" -eq 0 ]

  gotg saves push --all
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already up to date"* ]]
  # Still one generation: bundling is deterministic, so nothing looked changed.
  run bash -c "ls '$SERVER_ROOT/Games/.gotg/saves/env-n64/gen' | wc -l"
  [ "$output" = "1" ]
}

@test "two machines editing the same save is refused, not merged" {
  write_save "zelda.ram" "from-a"
  gotg saves push --all

  second_device
  gotg saves pull --all
  [ "$status" -eq 0 ]

  # A plays on, and pushes.
  first_device
  write_save "zelda.ram" "from-a-again"
  gotg saves push --all
  [ "$status" -eq 0 ]

  # B has been playing too, and is still on the older generation.
  second_device
  write_save "zelda.ram" "from-b"
  gotg saves push --all
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"moved on since this machine last synced"* ]]
  [[ "$stderr" == *"--force"* ]]
  [[ "$stderr" == *"gotg saves pull"* ]]

  # And the remote is untouched: A's generation 2 is still what latest points at.
  run jq -r '.generation' "$SERVER_ROOT/Games/.gotg/saves/env-n64/latest.json"
  [ "$output" = "2" ]
  run bash -c "cd '$SERVER_ROOT/Games/.gotg/saves/env-n64/gen' && ls | wc -l"
  [ "$output" = "2" ]
}

@test "forcing keeps the generation it overtook" {
  write_save "zelda.ram" "from-a"
  gotg saves push --all
  second_device
  gotg saves pull --all
  first_device
  write_save "zelda.ram" "from-a-again"
  gotg saves push --all

  second_device
  write_save "zelda.ram" "from-b"
  gotg saves push --all --force
  [ "$status" -eq 0 ]

  # Three generations: nothing a push does, forced or not, removes a bundle.
  run bash -c "cd '$SERVER_ROOT/Games/.gotg/saves/env-n64/gen' && ls | wc -l"
  [ "$output" = "3" ]
  # And the loser still restores.
  first_device
  gotg saves pull --all
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from-b" ]
}

@test "a pull archives what was here before it overwrites it" {
  write_save "zelda.ram" "from-a"
  gotg saves push --all

  second_device
  write_save "zelda.ram" "local-work-on-b"
  gotg saves pull --all
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from-a" ]

  # The overwritten tree is still recoverable.
  run bash -c "ls '$GOTG_SAVES_DIR/local/env-n64'/*.tar.zst | wc -l"
  [ "$output" = "1" ]
  run bash -c "tar -xOf '$GOTG_SAVES_DIR/local/env-n64'/*.tar.zst saves/zelda.ram"
  [ "$output" = "local-work-on-b" ]
}

@test "a pull leaves alone what the bundle says nothing about" {
  # The state directory holds more than saves — a Harkinian .o2r is tens of
  # megabytes and must survive a pull that knows nothing about it.
  write_save "zelda.ram" "from-a"
  gotg saves push --all

  second_device
  printf 'derived-from-rom' >"$STATE/saves/game.o2r"
  printf 'unrelated' >"$STATE/settings.cfg"
  gotg saves pull --all
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/game.o2r")" = "derived-from-rom" ]
  [ "$(cat "$STATE/settings.cfg")" = "unrelated" ]
}

@test "an excluded file never reaches the bundle" {
  write_save "zelda.ram" "real-save"
  printf 'derived-from-rom' >"$STATE/saves/game.o2r"
  gotg saves push --all
  [ "$status" -eq 0 ]

  run bash -c "tar -tf '$SERVER_ROOT/Games/.gotg/saves/env-n64/gen'/*.tar.zst"
  [[ "$output" == *"saves/zelda.ram"* ]]
  [[ "$output" != *".o2r"* ]]
}

@test "a save set over the limit is refused before anything uploads" {
  write_save "zelda.ram" "small"
  head -c 200000 /dev/urandom >"$STATE/saves/huge.bin"
  export GOTG_SAVES_MAX_BYTES=65536

  gotg saves push --all
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"over the"* ]]
  # Named, so it is obvious which glob went wrong.
  [[ "$stderr" == *"huge.bin"* ]]
  [ ! -d "$SERVER_ROOT/Games/.gotg/saves/env-n64" ]
}

@test "a bundle that climbs out of the state directory is refused" {
  # Hand-built on the server, with latest.json pointing at it and the hash
  # honest, so the only thing standing between it and /tmp is the member check.
  write_save "zelda.ram" "from-a"
  gotg saves push --all

  local gen="$SERVER_ROOT/Games/.gotg/saves/env-n64/gen"
  local evil="$TEST_TMP/evil"
  mkdir -p "$evil"
  printf 'pwned' >"$evil/pwned"
  tar -C "$evil" --transform 's|pwned|../../../../tmp/gotg-pwned|' \
    --zstd -cf "$TEST_TMP/evil.tar.zst" pwned

  local hash
  hash="$(sha256sum "$TEST_TMP/evil.tar.zst" | cut -d' ' -f1)"
  cp "$TEST_TMP/evil.tar.zst" "$gen/000009-${hash:0:12}.tar.zst"
  jq -n --arg h "$hash" --arg b "gen/000009-${hash:0:12}.tar.zst" \
    '{version:1, attr:"env-n64", generation:9, hash:$h, bundle:$b,
      size:100, files:1, device:"evil", written_at:"2026-08-01T00:00:00Z"}' \
    >"$SERVER_ROOT/Games/.gotg/saves/env-n64/latest.json"

  second_device
  gotg saves pull --all
  [ "$status" -ne 0 ]
  [ ! -e /tmp/gotg-pwned ]
}

@test "a bundle holding a symlink is refused outright" {
  write_save "zelda.ram" "from-a"
  gotg saves push --all

  local gen="$SERVER_ROOT/Games/.gotg/saves/env-n64/gen"
  local evil="$TEST_TMP/evil-link/saves"
  mkdir -p "$evil"
  ln -s /etc/passwd "$evil/zelda.ram"
  tar -C "$TEST_TMP/evil-link" --zstd -cf "$TEST_TMP/link.tar.zst" saves

  local hash
  hash="$(sha256sum "$TEST_TMP/link.tar.zst" | cut -d' ' -f1)"
  cp "$TEST_TMP/link.tar.zst" "$gen/000009-${hash:0:12}.tar.zst"
  jq -n --arg h "$hash" --arg b "gen/000009-${hash:0:12}.tar.zst" \
    '{version:1, attr:"env-n64", generation:9, hash:$h, bundle:$b,
      size:100, files:1, device:"evil", written_at:"2026-08-01T00:00:00Z"}' \
    >"$SERVER_ROOT/Games/.gotg/saves/env-n64/latest.json"

  second_device
  gotg saves pull --all
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not a plain file"* ]]
  [ ! -L "$STATE/saves/zelda.ram" ]
}

@test "a corrupted bundle is refused and the local saves are untouched" {
  write_save "zelda.ram" "from-a"
  gotg saves push --all

  second_device
  write_save "zelda.ram" "mine"
  # Corrupt it where it lies, leaving latest.json claiming the original hash.
  local bundle
  bundle="$(echo "$SERVER_ROOT/Games/.gotg/saves/env-n64/gen"/*.tar.zst)"
  printf 'rubbish' >"$bundle"

  gotg saves pull --all
  [ "$status" -ne 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "mine" ]
}

@test "status writes nothing and survives the server being gone" {
  write_save "zelda.ram"
  gotg saves push --all
  stop_server

  gotg saves status --all
  [ "$status" -eq 0 ]
  [[ "$output$stderr" == *"unreachable"* ]]
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

@test "a game id names the environment its saves actually belong to" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  write_save "zelda.ram"

  gotg saves push usa.zelda
  [ "$status" -eq 0 ]
  # Several games share one environment, so saying "pushing usa.zelda" would be
  # a small lie about what moved.
  [[ "$stderr" == *"usa.zelda runs in env-n64"* ]]
}
