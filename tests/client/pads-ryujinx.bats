#!/usr/bin/env bats
# Ryujinx bindings are kept, not generated: the last working input_config is
# snapshotted beside the environment, and a config that has lost its bindings —
# the settings screen deletes a sleeping pad's entry on save — gets them back
# before the launch.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

config_path() { printf '%s/env-switch/config/Ryujinx/Config.json' "$GOTG_STATE_DIR/env"; }

snap_path() { printf '%s/env-switch/input-config.json' "$GOTG_STATE_DIR/env"; }

# The derivation's pads.json, saying whose bindings these are.
fake_ryujinx_env() {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  jq -n '{emulator: "ryujinx"}' >"$GOTG_ROOTS_DIR/env-switch/share/gotg/pads.json"
}

# A config the way Ryujinx writes one: more keys than input_config, all of
# which must survive anything done here.
write_ryujinx_config() {
  local bindings="$1"
  mkdir -p "$(dirname "$(config_path)")"
  jq -n --argjson input "$bindings" \
    '{version: 70, show_confirm_exit: true, input_config: $input}' >"$(config_path)"
}

BOUND='[{"id": "0-00000005-045e-0000-8e02-000030110000", "name": "Xbox 360 Controller (0)", "player_index": "Player1", "backend": "GamepadSDL2"}]'

@test "a working binding is snapshotted" {
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ -s "$(snap_path)" ]
  [ "$(jq -r '.[0].name' "$(snap_path)")" = "Xbox 360 Controller (0)" ]
}

@test "a config that lost its bindings gets the snapshot back" {
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  pads_configure env-switch
  write_ryujinx_config '[]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq -r '.input_config[0].id' "$(config_path)")" = \
    "0-00000005-045e-0000-8e02-000030110000" ]
  [[ "$output" == *"restored"* ]]
}

@test "a restore touches nothing but input_config" {
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  pads_configure env-switch
  write_ryujinx_config '[]'
  pads_configure env-switch
  [ "$(jq -r '.version' "$(config_path)")" = "70" ]
  [ "$(jq -r '.show_confirm_exit' "$(config_path)")" = "true" ]
}

@test "no config yet — a first launch — is left entirely alone" {
  fake_ryujinx_env
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ ! -e "$(config_path)" ]
  [ ! -e "$(snap_path)" ]
}

@test "empty bindings with no snapshot stay empty" {
  fake_ryujinx_env
  write_ryujinx_config '[]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq '.input_config | length' "$(config_path)")" = "0" ]
}

@test "a rebind replaces the snapshot rather than fighting it" {
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  pads_configure env-switch
  write_ryujinx_config '[{"id": "0-00000003-28de-0000-ff11-000001000000", "name": "Steam Virtual Gamepad (0)"}]'
  pads_configure env-switch
  [ "$(jq -r '.[0].name' "$(snap_path)")" = "Steam Virtual Gamepad (0)" ]
}

@test "a corrupt config is not ours to fix" {
  fake_ryujinx_env
  mkdir -p "$(dirname "$(config_path)")"
  printf 'not json' >"$(config_path)"
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(cat "$(config_path)")" = "not json" ]
}

@test "an environment whose pads.json names another emulator is untouched" {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  jq -n '{emulator: "somethingelse"}' >"$GOTG_ROOTS_DIR/env-switch/share/gotg/pads.json"
  write_ryujinx_config "$BOUND"
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ ! -e "$(snap_path)" ]
}

@test "bindings lost to null or to a missing key restore the same as []" {
  # Both shapes exist in the wild: an upgrade can null the array, and a
  # rejected config rewrite can drop the key.
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  pads_configure env-switch
  local wiped
  for wiped in '{version: 70, input_config: null}' '{version: 70}'; do
    jq -n "$wiped" >"$(config_path)"
    run pads_configure env-switch
    [ "$status" -eq 0 ]
    [ "$(jq -r '.input_config[0].name' "$(config_path)")" = "Xbox 360 Controller (0)" ]
  done
}

@test "a corrupt snapshot restores nothing and leaves no residue" {
  fake_ryujinx_env
  write_ryujinx_config '[]'
  printf 'not json' >"$(snap_path)"
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq '.input_config | length' "$(config_path)")" = "0" ]
  [ ! -e "$(config_path).part" ]
}

@test "the launcher's jq pins and a restore compose — neither undoes the other" {
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  pads_configure env-switch
  write_ryujinx_config '[]'
  pads_configure env-switch
  # The exact edit switch.nix's preLaunch applies after pads run.
  jq '.update_checker_type = "Off" | .show_confirm_exit = false' \
    "$(config_path)" >"$(config_path).gotg" && mv "$(config_path).gotg" "$(config_path)"
  [ "$(jq -r '.input_config[0].name' "$(config_path)")" = "Xbox 360 Controller (0)" ]
  [ "$(jq -r '.show_confirm_exit' "$(config_path)")" = "false" ]
  # And the launch after that snapshots the restored set rather than losing it.
  pads_configure env-switch
  [ "$(jq -r '.[0].name' "$(snap_path)")" = "Xbox 360 Controller (0)" ]
}

@test "two pads with unicode names survive the round trip in order" {
  fake_ryujinx_env
  local pads='[{"id": "0-00000005-057e-0000-2009-000000000000", "name": "プロコン (0)", "player_index": "Player1"}, {"id": "0-00000003-28de-0000-ff11-000001000000", "name": "Ödög pad² (1)", "player_index": "Player2"}]'
  write_ryujinx_config "$pads"
  pads_configure env-switch
  write_ryujinx_config '[]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq '.input_config | length' "$(config_path)")" = "2" ]
  [ "$(jq -r '.input_config[0].name' "$(config_path)")" = "プロコン (0)" ]
  [ "$(jq -r '.input_config[1].player_index' "$(config_path)")" = "Player2" ]
}

@test "snapshotting is idempotent — a second identical launch rewrites nothing" {
  fake_ryujinx_env
  write_ryujinx_config "$BOUND"
  pads_configure env-switch
  # A rewrite goes through mv, which is a new inode; a skipped one is not.
  local before
  before="$(stat -c '%i' "$(snap_path)")"
  pads_configure env-switch
  [ "$(stat -c '%i' "$(snap_path)")" = "$before" ]
  [ ! -e "$(snap_path).part" ]
}

# --- motion -----------------------------------------------------------------
#
# Ryujinx keeps motion in each player's own entry, off by default, so a pad
# with a gyro is silent in a game that wants one until somebody finds the
# settings page. These check that the entry is filled in for the pad that has
# the hardware, and only for that one.

# A stand-in gotg-pads. Nothing above this point sets one, which is why the
# tests before it see no controller and leave motion entirely alone.
fake_pads() {
  export GOTG_PADS="$TEST_TMP/bin/gotg-pads"
  mkdir -p "$TEST_TMP/bin"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'cat "$GOTG_PADS_FIXTURE"\n'
  } >"$GOTG_PADS"
  chmod +x "$GOTG_PADS"
  export GOTG_PADS_FIXTURE="$TEST_TMP/pads.json"
  printf '%s' "$1" >"$GOTG_PADS_FIXTURE"
}

GYRO_PAD='[{"name": "Steam Controller", "guid": "03002854de2800000413000002006800",
            "slot": 0, "gamepad": true, "motion": true}]'
FLAT_PAD='[{"name": "Xbox 360 Controller", "guid": "030000005e0400008e02000010010000",
            "slot": 0, "gamepad": true, "motion": false}]'

@test "a pad with a gyro gets a motion block Ryujinx will use" {
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Steam Controller (0)", "backend": "GamepadSDL2"}]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq -r '.input_config[0].motion.motion_backend' "$(config_path)")" = "GamepadDriver" ]
  [ "$(jq -r '.input_config[0].motion.enable_motion' "$(config_path)")" = "true" ]
  # Ryujinx's own defaults, so the numbers match what its settings page offers.
  [ "$(jq -r '.input_config[0].motion.sensitivity' "$(config_path)")" = "100" ]
  [ "$(jq -r '.input_config[0].motion.gyro_deadzone' "$(config_path)")" = "1" ]
  [[ "$output" == *"motion controls enabled"* ]]
}

@test "a pad without sensors is left without a motion block" {
  fake_ryujinx_env
  fake_pads "$FLAT_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Xbox 360 Controller (0)", "backend": "GamepadSDL2"}]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq -r '.input_config[0].motion // "none"' "$(config_path)")" = "none" ]
}

@test "only the entry belonging to the gyro pad is touched" {
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Xbox 360 Controller (0)", "backend": "GamepadSDL2"},
      {"id": "1-0000", "name": "Steam Controller (1)", "backend": "GamepadSDL2"}]'
  pads_configure env-switch
  [ "$(jq -r '.input_config[0].motion // "none"' "$(config_path)")" = "none" ]
  [ "$(jq -r '.input_config[1].motion.enable_motion' "$(config_path)")" = "true" ]
}

@test "a name Ryujinx truncated still matches its pad" {
  # Ryujinx stores at most 50 characters and marks the cut with an ellipsis.
  fake_ryujinx_env
  fake_pads '[{"name": "Some Extremely Verbose Controller Name That Runs Past Fifty",
               "guid": "0300", "slot": 0, "gamepad": true, "motion": true}]'
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Some Extremely Verbose Controller Name That Run... (0)",
       "backend": "GamepadSDL2"}]'
  pads_configure env-switch
  [ "$(jq -r '.input_config[0].motion.enable_motion' "$(config_path)")" = "true" ]
}

@test "a DSU server somebody configured is left alone" {
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Steam Controller (0)", "backend": "GamepadSDL2",
       "motion": {"motion_backend": "CemuHook", "enable_motion": true,
                  "dsu_server_host": "127.0.0.1", "dsu_server_port": 26760}}]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq -r '.input_config[0].motion.motion_backend' "$(config_path)")" = "CemuHook" ]
  [ "$(jq -r '.input_config[0].motion.dsu_server_port' "$(config_path)")" = "26760" ]
}

@test "a CemuHook block naming no server is healed to the gamepad" {
  # The settings page saves "use CemuHook" with whatever is in the host field,
  # including nothing: motion then polls a null server forever while a pad with
  # a working gyro sits in hand. That is a misfire, not a choice.
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Steam Controller (0)", "backend": "GamepadSDL2",
       "motion": {"motion_backend": "CemuHook", "enable_motion": true,
                  "slot": 0, "alt_slot": 0, "mirror_input": true,
                  "dsu_server_host": null, "dsu_server_port": 0,
                  "sensitivity": 80, "gyro_deadzone": 2}}]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq -r '.input_config[0].motion.motion_backend' "$(config_path)")" = "GamepadDriver" ]
  [ "$(jq -r '.input_config[0].motion.enable_motion' "$(config_path)")" = "true" ]
  # The numbers the player did set survive the heal; the DSU fields do not.
  [ "$(jq -r '.input_config[0].motion.sensitivity' "$(config_path)")" = "80" ]
  [ "$(jq -r '.input_config[0].motion.gyro_deadzone' "$(config_path)")" = "2" ]
  [ "$(jq -r '.input_config[0].motion | has("dsu_server_host")' "$(config_path)")" = "false" ]
  [[ "$output" == *"motion controls enabled"* ]]
}

@test "a keyboard entry is not a gamepad and gets no motion" {
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0", "name": "Steam Controller (0)", "backend": "WindowKeyboard"}]'
  pads_configure env-switch
  [ "$(jq -r '.input_config[0].motion // "none"' "$(config_path)")" = "none" ]
}

@test "motion is written once, not on every launch" {
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Steam Controller (0)", "backend": "GamepadSDL2"}]'
  pads_configure env-switch
  local before
  before="$(stat -c '%i' "$(config_path)")"
  run pads_configure env-switch
  [ "$(stat -c '%i' "$(config_path)")" = "$before" ]
  [[ "$output" != *"motion controls enabled"* ]]
  [ ! -e "$(config_path).part" ]
}

@test "the settings-screen restore brings the motion block back with it" {
  fake_ryujinx_env
  fake_pads "$GYRO_PAD"
  write_ryujinx_config \
    '[{"id": "0-0000", "name": "Steam Controller (0)", "backend": "GamepadSDL2"}]'
  pads_configure env-switch
  write_ryujinx_config '[]'
  run pads_configure env-switch
  [ "$status" -eq 0 ]
  [ "$(jq -r '.input_config[0].motion.enable_motion' "$(config_path)")" = "true" ]
}
