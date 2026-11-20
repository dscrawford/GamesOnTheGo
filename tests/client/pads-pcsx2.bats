#!/usr/bin/env bats
# Generating PCSX2 controller bindings.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

@test "a section is a DualShock 2 on the player's SDL device" {
  run pads_pcsx2_section 2 1
  [ "$status" -eq 0 ]
  [[ "$output" == "[Pad2]"$'\n'"Type = DualShock2"* ]]
  [[ "$output" == *"Cross = SDL-1/FaceSouth"* ]]
  [[ "$output" == *"Triangle = SDL-1/FaceNorth"* ]]
  [[ "$output" == *"L2 = SDL-1/+LeftTrigger"* ]]
  # Up on a stick is the negative half of its Y axis.
  [[ "$output" == *"LUp = SDL-1/-LeftY"* ]]
  [[ "$output" == *"RRight = SDL-1/+RightX"* ]]
}

@test "the rewrite replaces the pad sections and leaves the rest alone" {
  cat >"$TEST_TMP/PCSX2.ini" <<'INI'
[UI]
SettingsVersion = 1
[Pad1]
Type = DualShock2
Cross = Keyboard/X
[Pad2]
Type = DualShock2
Cross = SDL-7/FaceSouth
[Hotkeys]
LoadStateFromSlot1 = Keyboard/F1
INI

  pads_pcsx2_rewrite "$TEST_TMP/PCSX2.ini" "$(pads_pcsx2_section 1 0)"

  run cat "$TEST_TMP/PCSX2.ini"
  [[ "$output" != *"Keyboard/X"* ]]
  [[ "$output" != *"SDL-7"* ]]
  [[ "$output" == *"Cross = SDL-0/FaceSouth"* ]]
  [[ "$output" == *"SettingsVersion = 1"* ]]
  [[ "$output" == *"LoadStateFromSlot1 = Keyboard/F1"* ]]
}

@test "the rewrite makes the file when PCSX2 has not run yet" {
  pads_pcsx2_rewrite "$TEST_TMP/inis/PCSX2.ini" "$(pads_pcsx2_section 1 0)"
  grep -q '^\[Pad1\]$' "$TEST_TMP/inis/PCSX2.ini"
}

@test "one seated player leaves port two empty rather than PCSX2's default pad" {
  pads_seating() { printf '[{"name":"Pad"}]'; }
  pads_bin() { printf 'true'; }
  env_state_dir() { printf '%s' "$TEST_TMP/state"; }

  pads_pcsx2_configure env-ps2

  run cat "$TEST_TMP/state/config/PCSX2/inis/PCSX2.ini"
  [[ "$output" == *"[Pad1]"$'\n'"Type = DualShock2"* ]]
  [[ "$output" == *"[Pad2]"$'\n'"Type = None"* ]]
}

@test "a third player has no port to sit in" {
  pads_seating() { printf '[{"name":"A"},{"name":"B"},{"name":"C"}]'; }
  pads_bin() { printf 'true'; }
  env_state_dir() { printf '%s' "$TEST_TMP/state"; }

  pads_pcsx2_configure env-ps2

  run grep -c '^\[Pad' "$TEST_TMP/state/config/PCSX2/inis/PCSX2.ini"
  [ "$output" = "2" ]
}
