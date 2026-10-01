#!/usr/bin/env bats
# Save sync: generations of path-free bundles, and which side wins.
#
# The store lives inside the GOTG service now, and so do the conflict rules:
# a push carries the hash of the generation it descends from, and the service
# either advances the head atomically or answers with what is actually there.
# These run against the real service — the same process that runs in the
# cluster — because a mock would only ever test a copy of those rules.
#
# The service's data directory holds no path from any machine: members are
# relative to the environment's state directory, and where they land is
# decided by the machine extracting them. The two devices in these tests keep
# their state under different roots for exactly that reason — a save made
# under one layout must arrive cleanly into the other.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config

  export GOTG_DEVICE_ID=aaaa1111
  export GOTG_NOW=2026-08-01T12:00:00Z
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"

  fake_env env-n64 '["saves/**"]' '["saves/*.o2r"]'
  STATE="$GOTG_ENV_STATE_DIR/env-n64"
  mkdir -p "$STATE/saves"
}

teardown() {
  stop_saves_service
}

write_save() {
  mkdir -p "$STATE/saves"
  printf '%s' "${2:-contents}" >"$STATE/saves/$1"
}

# Everything a second machine is: its own state and config, the same service.
second_device() {
  export GOTG_STATE_DIR="$TEST_TMP/state-b"
  export GOTG_CONFIG_DIR="$TEST_TMP/config-b"
  export GOTG_CONFIG_FILE="$GOTG_CONFIG_DIR/config.json"
  export GOTG_CACHE_FILE="$GOTG_STATE_DIR/manifest.json"
  export GOTG_ROOTS_DIR="$GOTG_STATE_DIR/roots"
  export GOTG_ENV_STATE_DIR="$GOTG_STATE_DIR/env"
  export GOTG_SAVES_DIR="$GOTG_STATE_DIR/saves"
  export GOTG_DEVICE_ID=bbbb2222
  write_api_config
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

# Hand-publish a generation into the service's own store, as a tampered or
# hostile server would present it: well-formed metadata, bytes of our choosing.
publish_raw() {
  local bundle="$1" hash size
  hash="$(sha256sum "$bundle" | cut -d' ' -f1)"
  size="$(stat -c '%s' "$bundle")"
  mkdir -p "$SAVES_DATA_DIR/legacy/env-n64/gen"
  cp "$bundle" "$SAVES_DATA_DIR/legacy/env-n64/gen/000001-${hash:0:12}.tar.zst"
  jq -n --arg hash "$hash" --arg bundle "gen/000001-${hash:0:12}.tar.zst" \
    --argjson size "$size" \
    '{version: 1, attr: "env-n64", generation: 1, hash: $hash,
      bundle: $bundle, size: $size}' >"$SAVES_DATA_DIR/legacy/env-n64/current.json"
}

# --- what the service holds -------------------------------------------------

@test "the store holds a pointer and a generation, and no machine's paths" {
  write_save zelda.ram "hyrule"
  gotg saves push env-n64
  [ "$status" -eq 0 ]

  [ -f "$SAVES_DATA_DIR/legacy/env-n64/current.json" ]
  local bundle
  bundle="$(find "$SAVES_DATA_DIR/legacy/env-n64/gen" -name '*.tar.zst' | head -1)"
  [ -n "$bundle" ]

  # Members are relative to the state directory — nothing about this machine.
  run tar -tf "$bundle"
  [ "$output" = "saves/zelda.ram" ]
  # Nowhere in the store — pointer included — does this machine's layout appear.
  run ! grep -rF "$GOTG_ENV_STATE_DIR" "$SAVES_DATA_DIR"
}

@test "only the last ten generations survive a push" {
  local v
  for v in $(seq 1 12); do
    write_save zelda.ram "save $v"
    gotg saves push env-n64
    [ "$status" -eq 0 ]
  done

  run -0 bash -c "ls $SAVES_DATA_DIR/legacy/env-n64/gen | wc -l"
  [ "$output" -eq 10 ]
  # The oldest are the ones that went; generations still count upward.
  run -0 bash -c "ls $SAVES_DATA_DIR/legacy/env-n64/gen | sort | head -1"
  [[ "$output" == 000003-* ]]
}

# --- the round trip ---------------------------------------------------------

@test "a save pushed on one machine arrives on another with a different layout" {
  write_save zelda.ram "hyrule"
  gotg saves push env-n64
  [ "$status" -eq 0 ]

  # Machine b keeps its state under a different root — the save must land
  # where *it* keeps env-n64, because the bundle names no directory at all.
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
  [[ "$stderr" == *"already up to date"* ]]
}

@test "a pull keeps a local save the service has never heard of" {
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

  # The overwritten save is still recoverable, which is the whole promise.
  local archive
  archive="$(find "$GOTG_SAVES_DIR/local/env-n64" -name '*.tar.zst' | head -1)"
  [ -n "$archive" ]
  tar -xOf "$archive" saves/zelda.ram | grep -q "from machine b"
}

@test "only the last three pre-pull archives are kept" {
  write_save zelda.ram "remote"
  gotg saves push env-n64

  second_device
  local v
  for v in one two three four; do
    write_save zelda.ram "$v"
    gotg saves pull env-n64
    [ "$status" -eq 0 ]
  done
  run -0 bash -c "ls $GOTG_SAVES_DIR/local/env-n64 | wc -l"
  [ "$output" -le 3 ]
}

@test "pulling with nothing pushed yet says so and touches nothing" {
  write_save zelda.ram "mine"
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing has been pushed yet"* ]]
  [ "$(cat "$STATE/saves/zelda.ram")" = "mine" ]
}

@test "status says when this machine has work the service has not seen" {
  write_save zelda.ram
  gotg saves push env-n64
  write_save zelda.ram "played some more"
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"changed since the last sync"* ]]
}

@test "status tells nothing-pushed and unreachable apart" {
  write_save zelda.ram
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing pushed yet"* ]]

  stop_saves_service
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"unreachable"* ]]
}

@test "status reads only environments, not the build keys beside them" {
  # Every root has a `<attr>.by` beside it; each one used to be an
  # "invalid environment name" error in the middle of the report.
  printf 'key\n' >"$GOTG_ROOTS_DIR/env-n64.by"
  gotg saves status
  [ "$status" -eq 0 ]
  [[ "$stderr" != *"invalid environment name"* ]]
  [[ "$output$stderr" == *"env-n64"* ]]
}

@test "status works with no service configured at all" {
  rm -f "$GOTG_CONFIG_DIR/api.json"
  write_save zelda.ram
  gotg saves status env-n64
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64"* ]]
  [[ "$output" == *"none configured"* ]]
}

@test "a save filed under a console name with a space survives the trip" {
  # ares files saves under a directory named for the console, and every one of
  # those names has a space in it. The path crosses a tar, an HTTP body and an
  # extraction on the way, so this is the case that breaks first.
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'ocarina' >"$STATE/saves/Nintendo 64/zelda.ram"
  gotg saves push env-n64
  [ "$status" -eq 0 ]

  second_device
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/Nintendo 64/zelda.ram")" = "ocarina" ]
}

# --- which side wins --------------------------------------------------------

@test "two machines editing the same save is refused, not merged" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  write_save zelda.ram "from machine b"
  gotg saves push env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"has moved on since this machine last synced"* ]]

  # Neither side lost anything: machine b still has its own work, and the
  # service still has machine a's.
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine b" ]
  run -0 bash -c "ls $SAVES_DATA_DIR/legacy/env-n64/gen | wc -l"
  [ "$output" -eq 1 ]
}

@test "forcing a push replaces the head and keeps the losing generation" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  write_save zelda.ram "from machine b"
  gotg saves push --force env-n64
  [ "$status" -eq 0 ]
  # Both generations are there: the loser waits for retention, not deletion.
  run -0 bash -c "ls $SAVES_DATA_DIR/legacy/env-n64/gen | wc -l"
  [ "$output" -eq 2 ]

  first_device
  gotg saves pull env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine b" ]
}

# --- choosing between two saves ---------------------------------------------

@test "a push is signed with the name this machine logged in as" {
  jq '. + {name: "daniel-desktop"}' "$GOTG_CONFIG_DIR/api.json" >"$TEST_TMP/api.json"
  cp "$TEST_TMP/api.json" "$GOTG_CONFIG_DIR/api.json"
  write_save zelda.ram "from the desktop"
  gotg saves push env-n64
  [ "$(jq -r .device "$SAVES_DATA_DIR/legacy/env-n64/current.json")" = "daniel-desktop" ]
}

@test "check names a conflict, the machine each save is on, and when it changed" {
  export GOTG_DEVICE_NAME=daniel-desktop
  write_save zelda.ram "from the desktop"
  gotg saves push env-n64

  second_device
  export GOTG_DEVICE_NAME=daniel-deck
  gotg saves pull env-n64
  write_save zelda.ram "the deck played on"
  touch -d 2026-09-20T10:00:00Z "$STATE/saves/zelda.ram"
  first_device
  export GOTG_DEVICE_NAME=daniel-desktop
  write_save zelda.ram "the desktop played on"
  gotg saves push --force env-n64

  second_device
  export GOTG_DEVICE_NAME=daniel-deck
  gotg saves check env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r .state <<<"$output")" = "conflict" ]
  [ "$(jq -r .here.device <<<"$output")" = "daniel-deck" ]
  [ "$(jq -r .here.updated <<<"$output")" = "2026-09-20T10:00:00Z" ]
  [ "$(jq -r .remote.device <<<"$output")" = "daniel-desktop" ]
  [ -n "$(jq -r .remote.updated <<<"$output")" ]
}

@test "check tells the one-sided cases apart, and a synced save is as old as its generation" {
  write_save zelda.ram "one"
  gotg saves check env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r .state <<<"$output")" = "here-only" ]

  gotg saves push env-n64
  gotg saves check env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r .state <<<"$output")" = "same" ]

  second_device
  gotg saves pull env-n64
  # Pulled files carry the bundle's zeroed times; the save is as old as the
  # generation it came from, not 1970.
  gotg saves check env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r .state <<<"$output")" = "same" ]
  [[ "$(jq -r .here.updated <<<"$output")" == 20* ]]
  [[ "$(jq -r .here.updated <<<"$output")" != 1970* ]]

  write_save zelda.ram "two"
  gotg saves check env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r .state <<<"$output")" = "here-newer" ]
  gotg saves push env-n64

  first_device
  gotg saves check env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r .state <<<"$output")" = "remote-newer" ]
}

@test "keeping this machine's save makes it the service's, and the other waits for retention" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64
  second_device
  write_save zelda.ram "from machine b"

  gotg saves keep env-n64 here
  [ "$status" -eq 0 ]
  run -0 bash -c "ls $SAVES_DATA_DIR/legacy/env-n64/gen | wc -l"
  [ "$output" -eq 2 ]
  first_device
  gotg saves pull env-n64
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine b" ]
}

@test "keeping the service's save archives this machine's first" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64
  second_device
  write_save zelda.ram "from machine b"

  gotg saves keep env-n64 remote
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine a" ]
  run -0 bash -c "ls $GOTG_SAVES_DIR/local/env-n64 2>/dev/null | wc -l"
  [ "$output" -ge 1 ]
}

@test "keep asks which side" {
  gotg saves keep env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"here|remote"* ]]
}

# --- going back to a save ---------------------------------------------------
#
# What the picker's Saves list and the overlay's are made of: every save this
# user has for one environment -- the service's kept generations and this
# machine's own archives -- and putting one of them back.

push_save() {
  write_save zelda.ram "$1"
  gotg saves push env-n64
  [ "$status" -eq 0 ]
}

@test "list shows the service's kept saves newest first, and which one is here" {
  push_save one
  push_save two
  gotg saves list env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.attr' <<<"$output")" = env-n64 ]
  [ "$(jq -c '[.saves[] | .generation]' <<<"$output")" = '[2,1]' ]
  [ "$(jq -c '[.saves[] | .here]' <<<"$output")" = '[true,false]' ]
  [ "$(jq -r '.saves[0].source' <<<"$output")" = remote ]
  [ "$(jq -r '.saves[0].id' <<<"$output")" = remote:2 ]
  [ "$(jq -r '.saves[1].device' <<<"$output")" = aaaa1111 ]
}

@test "list includes this machine's archives, but not twice what the service has" {
  push_save "from machine a"
  second_device
  write_save zelda.ram "from machine b"
  gotg saves pull env-n64
  gotg saves list env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq '.saves | length' <<<"$output")" -eq 2 ]
  [[ "$(jq -r '.saves[] | select(.source == "local") | .id' <<<"$output")" == local:* ]]
  [ "$(jq -r '.saves[] | select(.source == "local") | .device' <<<"$output")" = bbbb2222 ]
}

@test "list says whether a game has its environment to itself" {
  gotg saves list env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.dedicated' <<<"$output")" = false ]
  fake_env env-n64-usa_zelda '["saves/**"]'
  gotg saves list env-n64-usa_zelda --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.dedicated' <<<"$output")" = true ]
  [ "$(jq '.saves' <<<"$output")" = '[]' ]
}

@test "list with no service still shows this machine's archives" {
  push_save "pushed"
  second_device
  write_save zelda.ram "only here"
  gotg saves pull env-n64
  rm -f "$GOTG_CONFIG_DIR/api.json"
  gotg saves list env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.offline' <<<"$output")" = true ]
  [ "$(jq -r '[.saves[] | .source] | unique | join(",")' <<<"$output")" = local ]
}

@test "restoring an older save puts it back, and archives what was here first" {
  push_save one
  push_save two
  gotg saves restore env-n64 remote:1
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = one ]
  local archive
  archive="$(find "$GOTG_SAVES_DIR/local/env-n64" -name '*.tar.zst' | head -1)"
  tar -xOf "$archive" saves/zelda.ram | grep -q two
}

@test "a restored save is pushed as the newest, not refused as a conflict" {
  push_save one
  push_save two
  gotg saves restore env-n64 remote:1
  [ "$status" -eq 0 ]
  gotg saves push env-n64
  [ "$status" -eq 0 ]
  gotg saves list env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.saves[0].generation' <<<"$output")" -eq 3 ]
  [ "$(jq -r '.saves[0].here' <<<"$output")" = true ]
  second_device
  gotg saves pull env-n64
  [ "$(cat "$STATE/saves/zelda.ram")" = one ]
}

@test "a restore takes away the save files the chosen save did not have" {
  push_save one
  write_save mario.ram "a later file"
  printf 'archive' >"$STATE/saves/port.o2r"
  gotg saves push env-n64
  gotg saves restore env-n64 remote:1
  [ "$status" -eq 0 ]
  [ ! -e "$STATE/saves/mario.ram" ]
  # Not a save: the restore has no business with it.
  [ "$(cat "$STATE/saves/port.o2r")" = archive ]
}

@test "a launch does not pull back over a restore" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  push_save one
  push_save two
  gotg saves restore env-n64 remote:1
  [ "$status" -eq 0 ]
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = one ]
}

@test "a machine's own archive is restored with no service at all" {
  push_save "pushed"
  second_device
  write_save zelda.ram "only here"
  gotg saves pull env-n64
  gotg saves list env-n64 --json
  local id
  id="$(jq -r '.saves[] | select(.source == "local") | .id' <<<"$output")"
  rm -f "$GOTG_CONFIG_DIR/api.json"
  gotg saves restore env-n64 "$id"
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "only here" ]
}

@test "restore refuses what is not one of the saves" {
  push_save one
  for which in bogus remote:x remote:-1 local:../../etc/passwd local:nothing-here.tar.zst; do
    gotg saves restore env-n64 "$which"
    [ "$status" -ne 0 ]
  done
  [ "$(cat "$STATE/saves/zelda.ram")" = one ]
}

@test "a generation the service no longer keeps is said to be gone" {
  push_save one
  gotg saves restore env-n64 remote:7
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not kept"* ]]
}

# --- what a launch does -----------------------------------------------------

@test "booting a game takes the newest generation and places it itself" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine a" ]
}

@test "booting with unpushed local work leaves it alone and says why" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64

  second_device
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg saves pull env-n64

  # Both sides move on: b plays without pushing, a pushes.
  write_save zelda.ram "b played on"
  first_device
  write_save zelda.ram "a played on"
  gotg saves push --force env-n64

  second_device
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "b played on" ]
  [[ "$stderr" == *"both have new saves"* ]]
}

# --- the guards -------------------------------------------------------------

@test "it refuses to push something far too big to be a save" {
  write_save zelda.ram
  head -c 200000 /dev/zero >"$STATE/saves/huge.ram"
  GOTG_SAVES_MAX_BYTES=100000 gotg saves push env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"huge.ram"* ]]
  # Refused before anything was uploaded.
  [ ! -d "$SAVES_DATA_DIR/legacy/env-n64" ]
}

@test "a bundle that does not match its claimed hash is refused whole" {
  write_save zelda.ram "hyrule"
  gotg saves push env-n64

  # What a tampered store would serve: the pointer's bundle, different bytes.
  local bundle
  bundle="$(find "$SAVES_DATA_DIR/legacy/env-n64/gen" -name '*.tar.zst')"
  head -c 100 /dev/urandom >"$bundle"

  second_device
  write_save mine.ram "precious"
  gotg saves pull env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not the one the remote said it was"* ]]
  [ "$(cat "$STATE/saves/mine.ram")" = "precious" ]
}

@test "a bundle holding something no save glob claims is refused whole" {
  # The shape of an attack that wants to drop a file beside the emulator's
  # config: well-formed metadata, a member outside saves/.
  mkdir -p "$TEST_TMP/evil/elsewhere"
  printf 'pwned' >"$TEST_TMP/evil/elsewhere/startup.sh"
  tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    --zstd -C "$TEST_TMP/evil" -cf "$TEST_TMP/evil.tar.zst" elsewhere/startup.sh
  publish_raw "$TEST_TMP/evil.tar.zst"

  gotg saves pull env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not something this environment declares as a save"* ]]
  [ ! -e "$STATE/elsewhere/startup.sh" ]
}

@test "a bundle holding a symlink is refused whole" {
  mkdir -p "$TEST_TMP/evil/saves"
  ln -s /etc/passwd "$TEST_TMP/evil/saves/zelda.ram"
  tar --zstd -C "$TEST_TMP/evil" -cf "$TEST_TMP/evil.tar.zst" saves/zelda.ram
  publish_raw "$TEST_TMP/evil.tar.zst"

  gotg saves pull env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not a plain file"* ]]
  [ ! -e "$STATE/saves/zelda.ram" ] || [ ! -L "$STATE/saves/zelda.ram" ]
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

@test "setup proves the service answers with this token" {
  gotg saves setup
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"the token is accepted"* ]]
}

@test "setup with a refused token says so rather than pretending" {
  write_api_config "wrong-token"
  gotg saves setup
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"refused the token"* ]]
}
