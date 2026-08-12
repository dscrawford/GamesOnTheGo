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
