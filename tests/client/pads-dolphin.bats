#!/usr/bin/env bats
# Generating Dolphin controller bindings.
#
# The expected strings are what Dolphin itself wrote after a pad was bound by
# hand, so these compare against ground truth rather than against my reading of
# the format — the same rule pads.bats follows for ares.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

@test "a section names its device and binds the stick and D-pad together" {
  run pads_dolphin_section 1 "SDL/0/Steam Controller"
  [ "$status" -eq 0 ]

  [[ "$output" == "[GCPad1]"* ]]
  [[ "$output" == *"Device = SDL/0/Steam Controller"* ]]
  [[ "$output" == *'Buttons/A = `Button S`'* ]]
  # Either drives the stick: `|` is Dolphin's or, and each input name is
  # quoted on its own rather than the expression as a whole.
  [[ "$output" == *'Main Stick/Up = `Left Y+`|`Pad N`'* ]]
  # The D-pad stays the D-pad, so a game reading it still gets it.
  [[ "$output" == *'D-Pad/Up = `Pad N`'* ]]
  # Without a calibration line the stick reads short of its corners.
  [[ "$output" == *"Main Stick/Calibration = 100.00 141.42"* ]]
}

@test "the second player's section is the second port" {
  run pads_dolphin_section 2 "SDL/1/Pad"
  [[ "$output" == "[GCPad2]"* ]]
}

@test "the rewrite replaces the pad sections and leaves the rest alone" {
  cat >"$TEST_TMP/GCPadNew.ini" <<'INI'
[GCPad1]
Device = evdev/0/Some Old Thing
Buttons/A = `Button E`
[GCPad3]
Device = evdev/0/Unplugged Long Ago
[GBA1]
Device = keep/me
INI

  pads_dolphin_rewrite "$TEST_TMP/GCPadNew.ini" "$(pads_dolphin_section 1 "SDL/0/New Pad")"

  run cat "$TEST_TMP/GCPadNew.ini"
  [ "$status" -eq 0 ]
  # The stale device is gone rather than left to shadow the new one.
  [[ "$output" != *"Some Old Thing"* ]]
  [[ "$output" == *"Device = SDL/0/New Pad"* ]]
  # Every pad section goes, not only the one being written: a controller
  # unplugged since the last run would otherwise keep its port.
  [[ "$output" != *"Unplugged Long Ago"* ]]
  # A section that is not ours is untouched.
  [[ "$output" == *"[GBA1]"* ]]
  [[ "$output" == *"Device = keep/me"* ]]
}

@test "the rewrite copes with no file at all" {
  pads_dolphin_rewrite "$TEST_TMP/fresh.ini" "$(pads_dolphin_section 1 "SDL/0/Pad")"
  run cat "$TEST_TMP/fresh.ini"
  [[ "$output" == *"[GCPad1]"* ]]
}

@test "a port with no controller declared in it is ignored however well it is bound" {
  printf '[Core]\nSIDevice0 = 0\nCPUCore = 1\n[Interface]\nX = 1\n' >"$TEST_TMP/Dolphin.ini"
  pads_dolphin_ini_set "$TEST_TMP/Dolphin.ini" Core SIDevice0 6

  run grep -c "^SIDevice0 = 6$" "$TEST_TMP/Dolphin.ini"
  [ "$output" = "1" ]
  # Replaced, not appended.
  run grep -c "^SIDevice0" "$TEST_TMP/Dolphin.ini"
  [ "$output" = "1" ]
  # Neighbours in the section, and other sections, are left as they were.
  grep -q "^CPUCore = 1$" "$TEST_TMP/Dolphin.ini"
  grep -q "^X = 1$" "$TEST_TMP/Dolphin.ini"
}

@test "a missing key lands in its own section rather than the next one" {
  printf '[Core]\nCPUCore = 1\n[Interface]\nX = 1\n' >"$TEST_TMP/Dolphin.ini"
  pads_dolphin_ini_set "$TEST_TMP/Dolphin.ini" Core SIDevice1 6

  run cat "$TEST_TMP/Dolphin.ini"
  # Before [Interface], not after it.
  [[ "$output" == *"CPUCore = 1"$'\n'"SIDevice1 = 6"$'\n'"[Interface]"* ]]
}

@test "a missing section is created" {
  printf '[Interface]\nX = 1\n' >"$TEST_TMP/Dolphin.ini"
  pads_dolphin_ini_set "$TEST_TMP/Dolphin.ini" Core SIDevice0 6
  grep -q "^\[Core\]$" "$TEST_TMP/Dolphin.ini"
  grep -q "^SIDevice0 = 6$" "$TEST_TMP/Dolphin.ini"
}

@test "setting the same key twice changes nothing the second time" {
  printf '[Core]\nSIDevice0 = 0\n' >"$TEST_TMP/Dolphin.ini"
  pads_dolphin_ini_set "$TEST_TMP/Dolphin.ini" Core SIDevice0 6
  cp "$TEST_TMP/Dolphin.ini" "$TEST_TMP/once"
  pads_dolphin_ini_set "$TEST_TMP/Dolphin.ini" Core SIDevice0 6
  diff "$TEST_TMP/once" "$TEST_TMP/Dolphin.ini"
}
