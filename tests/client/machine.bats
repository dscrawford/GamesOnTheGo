#!/usr/bin/env bats
# Which machine a launch is on: the Deck by its firmware name, and whether a
# display other than its own panel is connected. See src/client/env/machine.sh.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
  source "$GOTG_ENV_DIR/machine.sh"
  unset GOTG_MACHINE GOTG_EXTERNAL_DISPLAY
  export GOTG_DMI_PRODUCT_FILE="$TEST_TMP/product_name"
  export GOTG_DRM_DIR="$TEST_TMP/drm"
  mkdir -p "$GOTG_DRM_DIR"
}

connector() {
  mkdir -p "$GOTG_DRM_DIR/card0-$1"
  printf '%s\n' "$2" >"$GOTG_DRM_DIR/card0-$1/status"
}

@test "a Deck is Jupiter or Galileo in the firmware's product name" {
  printf 'Galileo\n' >"$GOTG_DMI_PRODUCT_FILE"
  gotg_machine_detect
  [ "$GOTG_MACHINE" = deck ]
  printf 'Jupiter\n' >"$GOTG_DMI_PRODUCT_FILE"
  unset GOTG_MACHINE
  gotg_machine_detect
  [ "$GOTG_MACHINE" = deck ]
}

@test "anything else, or no firmware name at all, is a desktop" {
  printf 'B650 AORUS ELITE AX\n' >"$GOTG_DMI_PRODUCT_FILE"
  gotg_machine_detect
  [ "$GOTG_MACHINE" = desktop ]
  rm -f "$GOTG_DMI_PRODUCT_FILE"
  unset GOTG_MACHINE
  gotg_machine_detect
  [ "$GOTG_MACHINE" = desktop ]
}

@test "the machine's own panel is not an external display; a dock's DP is" {
  # A Deck OLED in its dock, as read off a real one: eDP-1 connected (the
  # panel), DP-3 connected (the dock), and a Writeback connector that says
  # "unknown".
  connector eDP-1 connected
  connector DP-1 disconnected
  connector DP-3 connected
  connector Writeback-1 unknown
  gotg_machine_detect
  [ "$GOTG_EXTERNAL_DISPLAY" = 1 ]
}

@test "the same Deck on the sofa has no external display" {
  connector eDP-1 connected
  connector DP-1 disconnected
  connector DP-3 disconnected
  connector Writeback-1 unknown
  gotg_machine_detect
  [ "$GOTG_EXTERNAL_DISPLAY" = 0 ]
}

@test "both answers can be given beforehand, and are kept" {
  printf 'Galileo\n' >"$GOTG_DMI_PRODUCT_FILE"
  connector DP-3 connected
  GOTG_MACHINE=desktop GOTG_EXTERNAL_DISPLAY=0 gotg_machine_detect
  export GOTG_MACHINE=desktop GOTG_EXTERNAL_DISPLAY=0
  gotg_machine_detect
  [ "$GOTG_MACHINE" = desktop ]
  [ "$GOTG_EXTERNAL_DISPLAY" = 0 ]
}
