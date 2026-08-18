#!/usr/bin/env bats
# Cemu motion: the one flag its profile hides.
#
# The profile below is Cemu's own output, copied from a real one — pugixml's
# tabs and element order included — because the whole point of editing in place
# is that a file this does not fully understand survives it.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs

  export XDG_CONFIG_HOME="$TEST_TMP/xdg"
  PROFILES="$XDG_CONFIG_HOME/Cemu/controllerProfiles"
  mkdir -p "$PROFILES"

  export GOTG_PADS="$TEST_TMP/bin/gotg-pads"
  mkdir -p "$TEST_TMP/bin"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'cat "$GOTG_PADS_FIXTURE"\n'
  } >"$GOTG_PADS"
  chmod +x "$GOTG_PADS"
  export GOTG_PADS_FIXTURE="$TEST_TMP/pads.json"
  pads_are "$GYRO_PAD"
}

GUID=03002854de2800000413000002006800

GYRO_PAD='[{"name": "Steam Controller", "guid": "03002854de2800000413000002006800",
            "slot": 0, "gamepad": true, "motion": true}]'
FLAT_PAD='[{"name": "Steam Controller", "guid": "03002854de2800000413000002006800",
            "slot": 0, "gamepad": true, "motion": false}]'

pads_are() { printf '%s' "$1" >"$GOTG_PADS_FIXTURE"; }

fake_cemu_env() {
  mkdir -p "$GOTG_ROOTS_DIR/env-wiiu/share/gotg"
  jq -n --argjson isolate "${1:-false}" \
    '{emulator: "cemu", isolate: $isolate}' \
    >"$GOTG_ROOTS_DIR/env-wiiu/share/gotg/pads.json"
}

# A profile as Cemu writes one. Tabs, not spaces.
write_profile() {
  local type="${1:-Wii U GamePad}" motion="${2:-}" uuid="${3:-0_$GUID}"
  {
    printf '<?xml version="1.0" encoding="UTF-8"?>\n'
    printf '<emulated_controller>\n'
    printf '\t<type>%s</type>\n' "$type"
    printf '\t<controller>\n'
    printf '\t\t<api>SDLController</api>\n'
    printf '\t\t<uuid>%s</uuid>\n' "$uuid"
    printf '\t\t<display_name>Steam Controller</display_name>\n'
    [[ -n "$motion" ]] && printf '\t\t<motion>%s</motion>\n' "$motion"
    printf '\t\t<rumble>0</rumble>\n'
    printf '\t\t<axis>\n\t\t\t<deadzone>0.25</deadzone>\n\t\t</axis>\n'
    printf '\t\t<mappings>\n'
    printf '\t\t\t<entry>\n\t\t\t\t<mapping>1</mapping>\n\t\t\t\t<button>1</button>\n\t\t\t</entry>\n'
    printf '\t\t</mappings>\n'
    printf '\t</controller>\n'
    printf '</emulated_controller>\n'
  } >"$PROFILES/controller0.xml"
}

motion_value() {
  sed -n 's:.*<motion>\(.*\)</motion>.*:\1:p' "$PROFILES/controller0.xml"
}

@test "a profile that says motion is off is turned on for a pad with a gyro" {
  fake_cemu_env
  write_profile "Wii U GamePad" false
  run pads_configure env-wiiu
  [ "$status" -eq 0 ]
  [ "$(motion_value)" = "true" ]
  [[ "$output" == *"motion controls enabled"* ]]
}

@test "a profile with no motion element gets one" {
  fake_cemu_env
  write_profile "Wii U GamePad"
  pads_configure env-wiiu
  [ "$(motion_value)" = "true" ]
  # Where Cemu itself writes it, so its own next save produces no diff.
  grep -A1 '<display_name>' "$PROFILES/controller0.xml" | grep -q '<motion>true</motion>'
  # And with Cemu's indentation, not ours.
  grep -q $'\t\t<motion>true</motion>' "$PROFILES/controller0.xml"
}

@test "a pad SDL reports no sensors for is left alone" {
  fake_cemu_env
  pads_are "$FLAT_PAD"
  write_profile "Wii U GamePad" false
  run pads_configure env-wiiu
  [ "$status" -eq 0 ]
  [ "$(motion_value)" = "false" ]
}

@test "a profile bound to some other pad is left alone" {
  fake_cemu_env
  write_profile "Wii U GamePad" false "0_030000005e0400008e02000010010000"
  pads_configure env-wiiu
  [ "$(motion_value)" = "false" ]
}

@test "two identical pads are told apart by their slot" {
  fake_cemu_env
  # Only the second one is the gyro pad here.
  pads_are '[{"name": "Steam Controller", "guid": "'"$GUID"'", "slot": 0,
              "gamepad": true, "motion": false},
             {"name": "Steam Controller", "guid": "'"$GUID"'", "slot": 1,
              "gamepad": true, "motion": true}]'
  write_profile "Wii U GamePad" false "0_$GUID"
  pads_configure env-wiiu
  [ "$(motion_value)" = "false" ]
  write_profile "Wii U GamePad" false "1_$GUID"
  pads_configure env-wiiu
  [ "$(motion_value)" = "true" ]
}

@test "a Pro Controller profile is told where the gyro actually goes" {
  fake_cemu_env
  write_profile "Wii U Pro Controller" false
  run pads_configure env-wiiu
  [ "$status" -eq 0 ]
  [ "$(motion_value)" = "true" ]
  [[ "$output" == *"no motion hardware"* ]]
  [[ "$output" == *"Wii U GamePad"* ]]
}

@test "everything the profile carries survives the edit" {
  fake_cemu_env
  write_profile "Wii U GamePad" false
  local before after
  before="$(grep -c '' "$PROFILES/controller0.xml")"
  pads_configure env-wiiu
  after="$(grep -c '' "$PROFILES/controller0.xml")"
  [ "$before" = "$after" ]
  grep -q '<mapping>1</mapping>' "$PROFILES/controller0.xml"
  grep -q '<deadzone>0.25</deadzone>' "$PROFILES/controller0.xml"
  grep -q '<rumble>0</rumble>' "$PROFILES/controller0.xml"
}

@test "a second launch rewrites nothing" {
  fake_cemu_env
  write_profile "Wii U GamePad" false
  pads_configure env-wiiu
  local before
  before="$(stat -c '%i' "$PROFILES/controller0.xml")"
  run pads_configure env-wiiu
  [ "$(stat -c '%i' "$PROFILES/controller0.xml")" = "$before" ]
  [[ "$output" != *"motion controls enabled"* ]]
  [ ! -e "$PROFILES/controller0.xml.gotg-tmp" ]
}

@test "a DSU profile is not an SDL one and is left alone" {
  fake_cemu_env
  write_profile "Wii U GamePad" false
  sed -i 's:<api>SDLController</api>:<api>DSUController</api>:' "$PROFILES/controller0.xml"
  pads_configure env-wiiu
  [ "$(motion_value)" = "false" ]
}

@test "an isolated environment is edited under its own state, not the player's" {
  fake_cemu_env true
  write_profile "Wii U GamePad" false
  local isolated="$GOTG_STATE_DIR/env/env-wiiu/config/Cemu/controllerProfiles"
  mkdir -p "$isolated"
  cp "$PROFILES/controller0.xml" "$isolated/"
  pads_configure env-wiiu
  [ "$(sed -n 's:.*<motion>\(.*\)</motion>.*:\1:p' "$isolated/controller0.xml")" = "true" ]
  # The player's own Cemu is somebody else's business.
  [ "$(motion_value)" = "false" ]
}

@test "no profiles at all is not an error" {
  fake_cemu_env
  rm -rf "$PROFILES"
  run pads_configure env-wiiu
  [ "$status" -eq 0 ]
}

@test "no controller attached leaves every profile as it was" {
  fake_cemu_env
  pads_are '[]'
  write_profile "Wii U GamePad" false
  run pads_configure env-wiiu
  [ "$status" -eq 0 ]
  [ "$(motion_value)" = "false" ]
}
