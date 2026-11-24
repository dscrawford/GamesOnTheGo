#!/usr/bin/env bats
# melonDS bindings: the ints its EmuInstance::joystickButtonDown decodes, and
# player one's joystick index as a game under danstick would see it.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

fixture() { cat "$BATS_TEST_DIRNAME/fixtures/gotg-pads-with-clone.json"; }

# The Steam Controller's map: buttons, one hat, sticks on axes 0 and 1.
steam_map() { fixture | jq -c '.[0].map'; }

@test "a face button is its index, by position: the DS's A is the pad's right button" {
  run pads_melonds_bindings "$(steam_map)" 0
  [ "$status" -eq 0 ]
  [ "$(jq '."Instance0.Joystick.A"' <<<"$output")" -eq 1 ]
  [ "$(jq '."Instance0.Joystick.B"' <<<"$output")" -eq 0 ]
  [ "$(jq '."Instance0.Joystick.X"' <<<"$output")" -eq 3 ]
  [ "$(jq '."Instance0.Joystick.Y"' <<<"$output")" -eq 2 ]
  [ "$(jq '."Instance0.Joystick.Start"' <<<"$output")" -eq 6 ]
}

@test "up is the hat and the left stick at once, in one int" {
  run pads_melonds_bindings "$(steam_map)" 0
  local up
  up="$(jq '."Instance0.Joystick.Up"' <<<"$output")"
  # The low half: 0x100 | hat 0 << 4 | SDL_HAT_UP.
  [ $((up & 0xFFFF)) -eq $((0x101)) ]
  # The axis half: present, axis 1, direction 1 (negative).
  [ $((up & 0x10000)) -ne 0 ]
  [ $(((up >> 24) & 0xF)) -eq 1 ]
  [ $(((up >> 20) & 0xF)) -eq 1 ]
}

@test "right is the positive x axis" {
  run pads_melonds_bindings "$(steam_map)" 0
  local right
  right="$(jq '."Instance0.Joystick.Right"' <<<"$output")"
  [ $(((right >> 24) & 0xF)) -eq 0 ]
  [ $(((right >> 20) & 0xF)) -eq 0 ]
  [ $((right & 0xFFFF)) -eq $((0x102)) ]
}

@test "an input the pad lacks is left out, not bound to button 0" {
  run pads_melonds_bindings '{"a": {"type": "button", "index": 4}}' 0
  [ "$(jq 'keys | length' <<<"$output")" -eq 2 ]
  [ "$(jq '."Instance0.Joystick.B"' <<<"$output")" -eq 4 ]
  [ "$(jq '."Instance0.JoystickID"' <<<"$output")" -eq 0 ]
}

@test "an axis with no button beside it keeps an empty low half" {
  run pads_melonds_bindings '{"lefty": {"type": "axis", "index": 1}}' 0
  local down
  down="$(jq '."Instance0.Joystick.Down"' <<<"$output")"
  [ $((down & 0xFFFF)) -eq $((0xFFFF)) ]
  [ $(((down >> 20) & 0xF)) -eq 0 ]
}

@test "under danstick the index counts only its clones" {
  # The game sees the clones and nothing else; player one's is the last
  # device gotg-pads listed, and the only clone published.
  local pads pad published
  pads="$(fixture)"
  published='[{"player":1,"guid":"0300724daa2a0000bb5b000001000000","name":"danstick Player 1"}]'
  pad="$(jq -c '.[5]' <<<"$pads")"
  run pads_melonds_index "$pads" "$pad" "$published"
  [ "$output" = "0" ]
}

@test "without danstick the index is SDL's own" {
  local pads pad
  pads="$(fixture)"
  pad="$(jq -c '.[2]' <<<"$pads")"
  run pads_melonds_index "$pads" "$pad" "[]"
  [ "$output" = "2" ]
}

@test "configure leaves the bindings where the environment's preLaunch reads them" {
  mkdir -p "$GOTG_ROOTS_DIR/env-nds/share/gotg"
  jq -n '{emulator: "melonds"}' >"$GOTG_ROOTS_DIR/env-nds/share/gotg/pads.json"
  export GOTG_PADS="$TEST_TMP/bin/gotg-pads"
  mkdir -p "$TEST_TMP/bin"
  fixture | jq -c '[.[0]]' >"$TEST_TMP/pads.json"
  make_stub "$GOTG_PADS" "cat '$TEST_TMP/pads.json'"
  run pads_configure env-nds
  [ "$status" -eq 0 ]
  local file="$GOTG_STATE_DIR/env/env-nds/melonds-pad.json"
  [ -s "$file" ]
  [ "$(jq '."Instance0.JoystickID"' "$file")" -eq 0 ]
  [ "$(jq '."Instance0.Joystick.A"' "$file")" -eq 1 ]
  [[ "$output" == *"melonDS (joystick 0)"* ]]
}

@test "with no pad at all the last bindings are kept" {
  mkdir -p "$GOTG_ROOTS_DIR/env-nds/share/gotg" "$GOTG_STATE_DIR/env/env-nds"
  jq -n '{emulator: "melonds"}' >"$GOTG_ROOTS_DIR/env-nds/share/gotg/pads.json"
  printf '{"Instance0.JoystickID": 1}' >"$GOTG_STATE_DIR/env/env-nds/melonds-pad.json"
  export GOTG_PADS="$TEST_TMP/bin/gotg-pads"
  mkdir -p "$TEST_TMP/bin"
  make_stub "$GOTG_PADS" "printf '[]'"
  run pads_configure env-nds
  [ "$status" -eq 0 ]
  [ "$(jq '."Instance0.JoystickID"' "$GOTG_STATE_DIR/env/env-nds/melonds-pad.json")" -eq 1 ]
}
