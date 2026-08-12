#!/usr/bin/env bats
# `gotg controllers` — reporting what SDL sees, and writing bindings on demand.
#
# Making a device readable is the host's job and is not tested here, because it
# is no longer this project's to do.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env

  # A stand-in gotg-pads, so these run with no controller attached.
  export GOTG_PADS="$TEST_TMP/bin/gotg-pads"
  mkdir -p "$TEST_TMP/bin"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'cat "$GOTG_PADS_FIXTURE"\n'
  } >"$GOTG_PADS"
  chmod +x "$GOTG_PADS"

  export GOTG_PADS_FIXTURE="$TEST_TMP/pads.json"
  jq -n '[{instance: 1, name: "Xbox Wireless Controller",
           guid: "050000005e040000", identity: "050000005e040000", slot: 0,
           vid: "045e", pid: "0b13", gamepad: true,
           path: "/dev/input/event24", evdev: "/dev/input/event24",
           steamSlot: null, map: {a: {type: "button", index: 0}}}]' \
    >"$GOTG_PADS_FIXTURE"

  mkdir -p "$TEST_TMP/data"
  cp "$(dirname "$GOTG_BIN")/../share/gotg/data/ares-pads.json" "$TEST_TMP/data/"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"
}

@test "list reports a controller in the terms bindings are written in" {
  gotg controllers list
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"Xbox Wireless Controller"* ]]
  # identity/slot is the string an emulator stores, so it is what you compare.
  [[ "$stderr" == *"binds as   050000005e040000/0"* ]]
  [[ "$stderr" == *"/dev/input/event24"* ]]
}

@test "list distinguishes a raw-HID pad from one with an evdev node" {
  jq -n '[{instance: 1, name: "Steam Controller", guid: "0300", identity: "0300",
           slot: 0, vid: "28de", pid: "1304", gamepad: true,
           path: "/dev/hidraw1", evdev: null, steamSlot: null,
           map: {a: {type: "button", index: 0}}}]' >"$GOTG_PADS_FIXTURE"

  gotg controllers list
  [ "$status" -eq 0 ]
  # Nothing that speaks evdev can bind this one, and that has to be visible.
  [[ "$stderr" == *"raw HID, no evdev node"* ]]
}

@test "list says plainly when SDL has no mapping for a pad" {
  jq -n '[{instance: 1, name: "Unknown Pad", guid: "ffff", identity: "ffff",
           slot: 0, vid: "0000", pid: "0000", gamepad: false,
           path: null, evdev: null, steamSlot: null, map: null}]' \
    >"$GOTG_PADS_FIXTURE"

  gotg controllers list
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"SDL does not recognise this pad"* ]]
}

@test "list is honest about an empty machine, and says whose job that is" {
  echo '[]' >"$GOTG_PADS_FIXTURE"
  gotg controllers list
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no controllers visible"* ]]
  [[ "$stderr" == *"steam-devices"* ]]
}

@test "apply writes bindings into a built environment without launching" {
  fake_env env-snes
  mkdir -p "$GOTG_ROOTS_DIR/env-snes/share/gotg"
  jq -n '{console: "SuperFamicom"}' >"$GOTG_ROOTS_DIR/env-snes/share/gotg/pads.json"

  local state="$GOTG_ENV_STATE_DIR/env-snes/data/ares"
  mkdir -p "$state"
  cat >"$state/settings.bml" <<'BML'
SuperFamicom
  Input
    Controller.Port.1
      Gamepad
        B: ;;
        A: ;;
BML

  gotg controllers apply --all
  [ "$status" -eq 0 ]
  grep -q "B: 050000005e040000/0/3/0;;" "$state/settings.bml"
}

@test "a console ares has never run gets its section made, not skipped" {
  fake_env env-snes
  mkdir -p "$GOTG_ROOTS_DIR/env-snes/share/gotg"
  jq -n '{console: "SuperFamicom"}' >"$GOTG_ROOTS_DIR/env-snes/share/gotg/pads.json"

  local state="$GOTG_ENV_STATE_DIR/env-snes/data/ares"
  mkdir -p "$state"
  # ares only writes a console's section after that console has run — which
  # used to leave the first launch of a new platform padless. The section is
  # written on the way in now, and ares' own settings survive beside it.
  printf 'Video\n  Driver: OpenGL\n' >"$state/settings.bml"

  gotg controllers apply --all
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"player 1"*"SuperFamicom"* ]]
  grep -qx "SuperFamicom" "$state/settings.bml"
  grep -q "Driver: OpenGL" "$state/settings.bml"
}

# Two controllers, in the order SDL reports them.
two_controllers() {
  jq -n '[{instance: 1, name: "Steam Controller", guid: "0300", identity: "0300",
           slot: 0, vid: "28de", pid: "1304", gamepad: true,
           path: "/dev/hidraw1", evdev: null, steamSlot: null,
           map: {a: {type: "button", index: 0}}},
          {instance: 2, name: "Xbox Wireless Controller", guid: "0500",
           identity: "0500", slot: 0, vid: "045e", pid: "0b13", gamepad: true,
           path: "/dev/input/event24", evdev: "/dev/input/event24",
           steamSlot: null, map: {a: {type: "button", index: 3}}}]' \
    >"$GOTG_PADS_FIXTURE"
}

@test "order numbers the controllers as the emulators will seat them" {
  two_controllers
  gotg controllers order
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"Player 1   Steam Controller"* ]]
  [[ "$stderr" == *"Player 2   Xbox Wireless Controller"* ]]
  # The identity is shown because that is what a binding is keyed on.
  [[ "$stderr" == *"0300/0"* ]]
}

@test "order says nothing is seated rather than nothing is attached" {
  jq -n '[{instance: 1, name: "Unknown Pad", guid: "ffff", identity: "ffff",
           slot: 0, vid: "0000", pid: "0000", gamepad: false, path: null,
           evdev: null, steamSlot: null, map: null}]' >"$GOTG_PADS_FIXTURE"
  gotg controllers order
  [ "$status" -eq 0 ]
  # A pad SDL cannot map is attached but unusable, and saying "no controllers"
  # would send someone looking at the wrong layer.
  [[ "$stderr" == *"no controllers to seat"* ]]
  [[ "$stderr" == *"controllers list"* ]]
}

@test "each controller binds to the console port of its own number" {
  two_controllers
  fake_env env-snes
  mkdir -p "$GOTG_ROOTS_DIR/env-snes/share/gotg"
  jq -n '{console: "SuperFamicom"}' >"$GOTG_ROOTS_DIR/env-snes/share/gotg/pads.json"

  local state="$GOTG_ENV_STATE_DIR/env-snes/data/ares"
  mkdir -p "$state"
  cat >"$state/settings.bml" <<'BML'
SuperFamicom
  Input
    Controller.Port.1
      Gamepad
        B: ;;
    Controller.Port.2
      Gamepad
        B: ;;
BML

  gotg controllers apply --all
  [ "$status" -eq 0 ]
  # Player one's pad to port one, player two's to port two — and the button
  # index differs between them, so a mix-up would be visible here.
  run awk '/Controller\.Port\.1/{p=1} /Controller\.Port\.2/{p=2} /B:/{print p, $2}' "$state/settings.bml"
  [ "${lines[0]}" = "1 0300/0/3/0;;" ]
  [ "${lines[1]}" = "2 0500/0/3/3;;" ]
}
