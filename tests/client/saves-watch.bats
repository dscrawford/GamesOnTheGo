#!/usr/bin/env bats
# Saves kept current while a game is played: the watcher beside every play,
# the one-shot sync a launch and the picker run, and the save-state slots
# the overlay lists.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config

  export GOTG_DEVICE_ID=aaaa1111
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"

  fake_env env-n64 '["saves/**"]' '["saves/*.o2r", "saves/*.bs[0-9]"]' "" \
    '{"files": "saves/*/{game}.bs{slot}", "slots": 2, "save": ["F5"], "load": ["F7"], "hotkeys": "ares"}'
  STATE="$GOTG_ENV_STATE_DIR/env-n64"
  mkdir -p "$STATE/saves"
  use_library

  # Fast enough for a test, slow enough that a write settles before a push.
  export GOTG_SAVES_WATCH_POLL_MS=100 GOTG_SAVES_WATCH_QUIET_MS=300 GOTG_SAVES_WATCH_EVERY_MS=600000
}

teardown() {
  [[ -z "${FOLLOWED:-}" ]] || kill "$FOLLOWED" 2>/dev/null || true
  [[ -z "${WATCHER:-}" ]] || kill "$WATCHER" 2>/dev/null || true
  stop_saves_service
}

write_save() {
  mkdir -p "$STATE/saves"
  printf '%s' "${2:-contents}" >"$STATE/saves/$1"
}

remote_generation() {
  local meta
  meta="$(curl -gfsS -H "Authorization: Bearer test-token" "$GOTG_SERVICE_URL/saves/${1:-env-n64}/meta" 2>/dev/null)" ||
    meta='{"generation": 0}'
  jq -r '.generation' <<<"$meta"
}

remote_is() { [ "$(remote_generation)" = "$1" ]; }
# The server counts the upload before the client, which is still reading the
# reply, writes it down: wait for both, not just the first.
pushed_recorded() {
  [ -n "$(jq -r '.pushed_hash // ""' "$GOTG_STATE_DIR/saves/env-n64.json" 2>/dev/null)" ]
}
watcher_gone() { ! kill -0 "$WATCHER" 2>/dev/null; }

# A process standing in for the game, and the watcher following it.
start_watching() {
  sleep 60 &
  FOLLOWED=$!
  "$GOTG_BIN" saves watch env-n64 --follow "$FOLLOWED" 2>"$TEST_TMP/watch.log" &
  WATCHER=$!
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

# --- the watcher ------------------------------------------------------------

@test "a save written during play is pushed once it has settled" {
  start_watching
  write_save zelda.ram "first"
  wait_for 10 remote_is 1
  wait_for 10 pushed_recorded
  write_save zelda.ram "second"
  wait_for 10 remote_is 2
}

@test "a save written before the watcher's first look is still pushed" {
  export GOTG_SAVES_WATCH_QUIET_MS=300
  write_save zelda.ram "between the launch and the watcher"
  start_watching
  wait_for 10 remote_is 1
}

@test "a save still being written is not pushed until it holds still" {
  export GOTG_SAVES_WATCH_QUIET_MS=1500
  start_watching
  write_save zelda.ram "a"
  sleep 0.5
  write_save zelda.ram "ab"
  sleep 0.5
  write_save zelda.ram "abc"
  sleep 0.7
  remote_is 0
  wait_for 10 remote_is 1
}

@test "when the game ends the watcher pushes what it wrote last, and leaves" {
  export GOTG_SAVES_WATCH_QUIET_MS=60000
  start_watching
  write_save zelda.ram "last words"
  sleep 0.3
  remote_is 0
  kill "$FOLLOWED"
  wait_for 10 remote_is 1
  wait_for 10 watcher_gone
}

@test "the watcher asks the service on a clock, so a push that failed is tried again" {
  export GOTG_SAVES_WATCH_QUIET_MS=60000 GOTG_SAVES_WATCH_EVERY_MS=500
  start_watching
  sleep 0.2
  write_save zelda.ram "on the clock"
  wait_for 10 remote_is 1
}

@test "a watcher with no service configured does nothing and exits" {
  rm -f "$GOTG_CONFIG_DIR/api.json"
  sleep 60 &
  FOLLOWED=$!
  run --separate-stderr "$GOTG_BIN" saves watch env-n64 --follow "$FOLLOWED"
  [ "$status" -eq 0 ]
}

@test "the watcher refuses a pid that is not one" {
  local args
  for args in "" "env-n64" "env-n64 --follow" "env-n64 --follow 7; rm -rf /" "env-n64 --follow 0" "env-n64 --follow -1"; do
    # shellcheck disable=SC2086
    run --separate-stderr "$GOTG_BIN" saves watch $args
    [ "$status" -ne 0 ] || {
      echo "accepted: $args"
      return 1
    }
  done
}

@test "a watcher started after the game is gone pushes once and leaves" {
  write_save zelda.ram "left behind"
  sleep 0.01 &
  local gone=$!
  wait "$gone"
  run --separate-stderr timeout 30 "$GOTG_BIN" saves watch env-n64 --follow "$gone"
  [ "$status" -eq 0 ]
  remote_is 1
}

@test "a game that wrote nothing pushes nothing when it ends" {
  start_watching
  sleep 0.5
  kill "$FOLLOWED"
  wait_for 10 watcher_gone
  remote_is 0
}

@test "a burst of writes is one push, and a clean exit after it adds none" {
  export GOTG_SAVES_WATCH_QUIET_MS=500
  start_watching
  local i
  for i in 1 2 3 4 5 6; do
    write_save zelda.ram "burst $i"
    sleep 0.15
  done
  wait_for 10 remote_is 1
  sleep 1.5
  remote_is 1
  kill "$FOLLOWED"
  wait_for 10 watcher_gone
  remote_is 1
}

@test "a remote that moved on during play is left alone, and the watcher keeps going" {
  write_save zelda.ram "from a"
  gotg saves push env-n64
  second_device
  gotg saves pull env-n64
  write_save zelda.ram "b played on"
  gotg saves push env-n64
  first_device
  start_watching
  write_save zelda.ram "a played on"
  wait_for 10 grep -q "both have new saves" "$TEST_TMP/watch.log"
  remote_is 2
  [ "$(cat "$STATE/saves/zelda.ram")" = "a played on" ]
  kill -0 "$WATCHER"
}

@test "a service that cannot be reached keeps the watcher alive and the saves where they are" {
  GOTG_SERVICE_URL="http://127.0.0.1:9" write_api_config
  start_watching
  write_save zelda.ram "offline"
  sleep 1.5
  kill -0 "$WATCHER"
  [ "$(cat "$STATE/saves/zelda.ram")" = "offline" ]
  kill "$FOLLOWED"
  wait_for 15 watcher_gone
}

@test "a watch interval that is not a plain number is replaced and said" {
  load_client_libs
  local bad
  for bad in "" abc 0 -5 1.5 "1; touch $TEST_TMP/pwned" 99999999999; do
    GOTG_X="$bad" run saves_watch_ms GOTG_X 4242
    [ "$status" -eq 0 ]
    [[ "$output" == *4242 ]]
  done
  [ ! -e "$TEST_TMP/pwned" ]
  GOTG_X=0007 run saves_watch_ms GOTG_X 4242
  [ "$output" = 7 ]
}

@test "the signature is none with no save files, moves on a write, and ignores what is excluded" {
  load_client_libs
  [ "$(saves_signature env-n64)" = none ]
  write_save zelda.ram "x"
  local base
  base="$(saves_signature env-n64)"
  [ "$base" != none ]
  [ "$(saves_signature env-n64)" = "$base" ]
  write_save zelda.ram "xx"
  [ "$(saves_signature env-n64)" != "$base" ]
  write_save zelda.ram "x"
  touch -r "$STATE/saves/zelda.ram" "$STATE/saves/zelda.ram"
  base="$(saves_signature env-n64)"
  printf 'noise' >"$STATE/cache.bin"
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'state' >"$STATE/saves/Nintendo 64/usa.zelda.bs1"
  ln -s "$STATE/saves/zelda.ram" "$STATE/saves/link.ram"
  [ "$(saves_signature env-n64)" = "$base" ]
}

@test "every play starts the watcher, so a game closed by itself still pushes" {
  write_save zelda.ram "played"
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  wait_for 10 remote_is 1
}

# The watchers following `pid`, by their command lines: no procps in the
# sandbox. The bracket keeps the grep from finding itself.
watchers_following() {
  grep -lsa -- "saves.wat[c]h.env-n64.--follow.$1" /proc/[0-9]*/cmdline 2>/dev/null | wc -l
}

@test "GOTG_SAVES_WATCH=0 keeps a launch from starting one" {
  load_client_libs
  sleep 60 &
  FOLLOWED=$!
  GOTG_SAVES_WATCH=0 saves_watch_start "$FOLLOWED" env-n64
  sleep 0.5
  [ "$(watchers_following "$FOLLOWED")" = 0 ]
  saves_watch_start "$FOLLOWED" env-n64
  wait_for 5 test "$(watchers_following "$FOLLOWED")" = 1 || [ "$(watchers_following "$FOLLOWED")" = 1 ]
  kill "$FOLLOWED"
  wait_for 10 test "$(watchers_following "$FOLLOWED")" = 0 || [ "$(watchers_following "$FOLLOWED")" = 0 ]
}

# --- sync -------------------------------------------------------------------

@test "sync pushes what is new here" {
  write_save zelda.ram "new here"
  gotg saves sync env-n64
  [ "$status" -eq 0 ]
  remote_is 1
  [[ "$stderr" == *"pushed generation 1"* ]]
}

@test "sync pulls what is new on the service, and a launch does the same" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64
  second_device
  gotg saves sync env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "from machine a" ]
}

@test "a launch pushes work left unpushed when the service has not moved on" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64
  write_save zelda.ram "played on, never pushed"
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  GOTG_SAVES_WATCH=0 gotg play usa.zelda
  [ "$status" -eq 0 ]
  remote_is 2
}

@test "sync leaves a conflict alone and says so" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64
  second_device
  gotg saves pull env-n64
  write_save zelda.ram "b played on"
  first_device
  write_save zelda.ram "a played on"
  gotg saves push --force env-n64
  second_device
  gotg saves sync env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$STATE/saves/zelda.ram")" = "b played on" ]
  [[ "$stderr" == *"both have new saves"* ]]
  remote_is 2
}

@test "sync --push-only never pulls" {
  write_save zelda.ram "from machine a"
  gotg saves push env-n64
  second_device
  gotg saves sync env-n64 --push-only
  [ "$status" -eq 0 ]
  [ ! -e "$STATE/saves/zelda.ram" ]
}

@test "sync --all walks every environment and a failing one does not stop the next" {
  fake_env env-snes '["saves/**"]'
  mkdir -p "$GOTG_ENV_STATE_DIR/env-snes/saves"
  head -c 200000 /dev/zero >"$GOTG_ENV_STATE_DIR/env-snes/saves/huge.ram"
  write_save zelda.ram "fine"
  GOTG_SAVES_MAX_BYTES=100000 gotg saves sync --all
  [ "$status" -eq 0 ]
  remote_is 1
  [[ "$stderr" == *"env-snes: could not look at the saves here"* ]]
}

# --- save states ------------------------------------------------------------

@test "states lists every slot, empty or with when it was written" {
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'state' >"$STATE/saves/Nintendo 64/usa.zelda.bs2"
  touch -d "2026-10-20T10:00:00Z" "$STATE/saves/Nintendo 64/usa.zelda.bs2"
  gotg saves states env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.slots | length' <<<"$output")" = 2 ]
  [ "$(jq -r '.slots[0].written_at' <<<"$output")" = "" ]
  [ "$(jq -r '.slots[1].written_at' <<<"$output")" = "2026-10-20T10:00:00Z" ]
  [ "$(jq -r '.slots[1].name' <<<"$output")" = "usa.zelda" ]
}

@test "states --lines is a slot a line for the overlay, the game it holds last" {
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'state' >"$STATE/saves/Nintendo 64/usa.zelda.bs1"
  gotg saves states env-n64 --lines
  [ "$status" -eq 0 ]
  [ "$(printf '%s\n' "$output" | wc -l)" = 2 ]
  [[ "${lines[0]}" == "slot:1	today, "*"	usa.zelda" ]]
  [[ "${lines[1]}" == "slot:2		" ]]
}

@test "the slots shown are the game being played, or any game when none is" {
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'old' >"$STATE/saves/Nintendo 64/usa.mario.bs1"
  GOTG_PLAY_TARGET="/games/n64/usa.zelda.z64" gotg saves states env-n64 --json
  [ "$(jq -r '.slots[0].written_at' <<<"$output")" = "" ]
  printf 'new' >"$STATE/saves/Nintendo 64/usa.zelda.bs1"
  GOTG_PLAY_TARGET="/games/n64/usa.zelda.z64" gotg saves states env-n64 --json
  [ "$(jq -r '.slots[0].name' <<<"$output")" = "usa.zelda" ]
  gotg saves states env-n64 --json
  [ "$(jq -r '.slots[0].name' <<<"$output")" != "" ]
}

@test "two-digit slot files are found, the newest file winning" {
  fake_env env-gc '["data/**"]' '[]' "" \
    '{"files": "data/dolphin-emu/StateSaves/*.s{slot2}", "slots": 3, "save": ["LEFTSHIFT", "F{slot}"], "load": ["F{slot}"]}'
  local d="$GOTG_ENV_STATE_DIR/env-gc/data/dolphin-emu/StateSaves"
  mkdir -p "$d"
  printf a >"$d/GALE01.s02"
  printf b >"$d/OTHER1.s02"
  touch -d "2026-10-20T10:00:00Z" "$d/GALE01.s02"
  touch -d "2026-10-21T10:00:00Z" "$d/OTHER1.s02"
  gotg saves states env-gc --json
  [ "$(jq -r '.slots | length' <<<"$output")" = 3 ]
  [ "$(jq -r '.slots[0].written_at' <<<"$output")" = "" ]
  [ "$(jq -r '.slots[1].name' <<<"$output")" = "OTHER1" ]
}

@test "a slot glob that climbs out of the state directory finds nothing" {
  fake_env env-n64 '["saves/**"]' '[]' "" \
    '{"files": "../../{game}.bs{slot}", "slots": 1, "save": ["F5"], "load": ["F7"]}'
  printf 'x' >"$GOTG_ENV_STATE_DIR/usa.zelda.bs1"
  GOTG_PLAY_TARGET="/games/n64/usa.zelda.z64" gotg saves states env-n64 --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.slots[0].written_at' <<<"$output")" = "" ]
}

@test "an emulator without save states lists none" {
  fake_env env-snes '["saves/**"]'
  gotg saves states env-snes --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.slots | length' <<<"$output")" = 0 ]
}

@test "the overlay is told the slots and the keys" {
  fake_env env-n64 '["saves/**"]' '[]' "" \
    '{"files": "saves/*/*.bs{slot}", "slots": 8, "save": ["LEFTSHIFT", "F{slot}"], "load": ["F{slot}"]}'
  load_client_libs
  run saves_states_spec env-n64
  [ "$output" = "slots=8;save=LEFTSHIFT+F{slot};load=F{slot}" ]
}

@test "a key the overlay's table lacks keeps the whole spec from being sent" {
  load_client_libs
  local key
  for key in "F5; rm -rf /" "F5 X" F13 PAGEUP LEFTMETA f5 "F{slots}" ""; do
    fake_env env-n64 '["saves/**"]' '[]' "" \
      "$(jq -nc --arg k "$key" '{files: "x", slots: 1, save: [$k], load: ["F7"]}')"
    run saves_states_spec env-n64
    [ -z "$output" ] || {
      echo "sent a spec with $key: $output"
      return 1
    }
  done
}

@test "a states block missing a piece sends no spec and does not fail the launch" {
  load_client_libs
  local block
  for block in '{"files":"x","slots":1,"load":["F7"]}' '{"files":"x","slots":1,"save":[],"load":["F7"]}' \
    '{"files":"x","slots":10,"save":["F5"],"load":["F7"]}' '{"files":"x","slots":"8; x","save":["F5"],"load":["F7"]}'; do
    fake_env env-n64 '["saves/**"]' '[]' "" "$block"
    run saves_states_spec env-n64
    [ "$status" -eq 0 ]
    [ -z "$output" ] || {
      echo "$block -> $output"
      return 1
    }
  done
}

@test "only a save state present is not a save to push" {
  mkdir -p "$STATE/saves/Nintendo 64"
  printf 'state' >"$STATE/saves/Nintendo 64/usa.zelda.bs1"
  gotg saves sync env-n64
  [ "$status" -eq 0 ]
  remote_is 0
  write_save zelda.ram "real"
  gotg saves sync env-n64
  remote_is 1
  printf 'another' >"$STATE/saves/Nintendo 64/usa.zelda.bs2"
  gotg saves sync env-n64
  remote_is 1
}

@test "ares hotkeys are not written for an emulator that did not ask" {
  fake_env env-n64 '["saves/**"]' '[]' "" '{"files":"x","slots":1,"save":["F5"],"load":["F7"]}'
  load_client_libs
  run states_prepare env-n64
  [ "$status" -eq 0 ]
  [ ! -e "$STATE/data/ares/settings.bml" ]
}

@test "a launch binds ares' save and load hotkeys where nobody has" {
  local bml="$STATE/data/ares/settings.bml"
  mkdir -p "$(dirname "$bml")"
  printf 'Video\n  Driver: OpenGL 3.2\nHotkey\n  ToggleFullscreen: ;;\n  SaveState: ;;\n  LoadState: 0x1/0/9;;\n' >"$bml"
  load_client_libs
  run states_prepare env-n64
  [ "$status" -eq 0 ]
  grep -q '^  SaveState: 0x1/0/5;;$' "$bml"
  grep -q '^  LoadState: 0x1/0/9;;$' "$bml"
  grep -q '^  ToggleFullscreen: ;;$' "$bml"
  grep -q '^  Driver: OpenGL 3.2$' "$bml"
}

@test "with no settings file yet, ares gets a Hotkey block to merge" {
  local bml="$STATE/data/ares/settings.bml"
  load_client_libs
  run states_prepare env-n64
  [ "$status" -eq 0 ]
  [ "$(cat "$bml")" = $'Hotkey\n  SaveState: 0x1/0/5;;\n  LoadState: 0x1/0/7;;' ]
  run states_prepare env-n64
  [ "$(grep -c SaveState "$bml")" = 1 ]
}
