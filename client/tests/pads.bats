#!/usr/bin/env bats
# Generating ares controller bindings.
#
# The expected strings here are not invented: they are what ares itself wrote
# after binding a Steam Controller by hand, so these tests are a comparison
# against ground truth rather than against my reading of the format.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
  ID=03002854de2800000413000002006800
  # As gotg-pads reports it: SDL's own mapping for the pad in question.
  MAP='{"a":{"type":"button","index":0},
        "b":{"type":"button","index":1},
        "back":{"type":"button","index":4},
        "start":{"type":"button","index":6},
        "dpup":{"type":"hat","index":0,"mask":1},
        "dpdown":{"type":"hat","index":0,"mask":4},
        "dpleft":{"type":"hat","index":0,"mask":8},
        "dpright":{"type":"hat","index":0,"mask":2},
        "lefttrigger":{"type":"axis","index":2,"min":-32768,"max":32767}}'
}

@test "a button becomes group 3 and its raw index" {
  run pads_ares_assignment "$ID" 0 "$MAP" a
  [ "$output" = "$ID/0/3/0" ]
  run pads_ares_assignment "$ID" 0 "$MAP" start
  [ "$output" = "$ID/0/3/6" ]
}

@test "a hat becomes a pair of pseudo-axes, exactly as ares wrote them" {
  # ares splits hat N into inputs 2N and 2N+1 — X then Y — with Lo for up and
  # left. These four strings are copied from a settings.bml ares produced.
  run pads_ares_assignment "$ID" 0 "$MAP" dpup
  [ "$output" = "$ID/0/1/1/Lo" ]
  run pads_ares_assignment "$ID" 0 "$MAP" dpdown
  [ "$output" = "$ID/0/1/1/Hi" ]
  run pads_ares_assignment "$ID" 0 "$MAP" dpleft
  [ "$output" = "$ID/0/1/0/Lo" ]
  run pads_ares_assignment "$ID" 0 "$MAP" dpright
  [ "$output" = "$ID/0/1/0/Hi" ]
}

@test "a second identical pad differs only by slot" {
  run pads_ares_assignment "$ID" 1 "$MAP" a
  [ "$output" = "$ID/1/3/0" ]
}

@test "an element this controller lacks binds nothing rather than guessing" {
  run pads_ares_assignment "$ID" 0 "$MAP" rightshoulder
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "the rewrite touches one console's Gamepad block and nothing else" {
  # Sibling blocks carry the same button names, and a whole-file rewrite would
  # take ares' other settings with it.
  cat >"$TEST_TMP/settings.bml" <<'BML'
Video
  Driver: OpenGL
SuperFamicom
  Input
    Controller.Port.1
      Gamepad
        Up: 0x1/0/57;;
        B: 0x1/0/95;;
      Rumble.Gamepad
        Up: ;;
        B: ;;
    Controller.Port.2
      Gamepad
        Up: ;;
        B: ;;
Nintendo64
  Input
    Controller.Port.1
      Gamepad
        Up: ;;
        B: ;;
BML

  pads_ares_rewrite "$TEST_TMP/settings.bml" SuperFamicom 1 \
    "$(jq -nc '{Up: "GUID/0/1/1/Lo", B: "GUID/0/3/0"}')"

  run cat "$TEST_TMP/settings.bml"
  [ "$status" -eq 0 ]

  # Port 1's Gamepad got both bindings.
  [[ "$output" == *"        Up: GUID/0/1/1/Lo;;"* ]]
  [[ "$output" == *"        B: GUID/0/3/0;;"* ]]
  # Exactly one line each — the siblings, the second port and the other console
  # are all still empty.
  run grep -c "GUID" "$TEST_TMP/settings.bml"
  [ "$output" = "2" ]
  # And an unrelated setting is untouched.
  grep -q "Driver: OpenGL" "$TEST_TMP/settings.bml"
}

@test "a console the table does not cover is left alone" {
  cat >"$TEST_TMP/settings.bml" <<'BML'
MegaDrive
  Input
    Controller.Port.1
      Gamepad
        Up: ;;
BML
  pads_ares_rewrite "$TEST_TMP/settings.bml" SuperFamicom 1 "$(jq -nc '{Up: "GUID/0/1/1/Lo"}')"
  run grep -c "GUID" "$TEST_TMP/settings.bml"
  [ "$output" = "0" ]
}
