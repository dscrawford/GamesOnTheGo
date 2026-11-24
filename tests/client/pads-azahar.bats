#!/usr/bin/env bats
# Azahar's bindings: player one's pad, by GUID and port, in SDL's gamepad numbering.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs

  export GOTG_PADS="$TEST_TMP/bin/gotg-pads"
  mkdir -p "$TEST_TMP/bin"
  make_stub "$GOTG_PADS" 'cat "$GOTG_PADS_FIXTURE"'
  export GOTG_PADS_FIXTURE="$TEST_TMP/pads.json"
  INI="$GOTG_STATE_DIR/env/env-3ds/config/azahar-emu/qt-config.ini"
}

GUID=03002854de2800000413000002006800
OTHER=030000005e0400008e02000010010000

pads_are() { printf '%s' "$1" >"$GOTG_PADS_FIXTURE"; }

pad() { printf '{"name": "%s", "guid": "%s", "slot": %s, "gamepad": true, "motion": %s, "map": {}}' "$@"; }

@test "player one is bound by GUID and port, A on the right face button" {
  pads_are "[$(pad "Steam Controller" "$GUID" 1 false), $(pad Other "$OTHER" 0 false)]"
  pads_azahar_configure env-3ds

  grep -qxF "profiles\\1\\button_a=\"engine:sdl,api:controller,guid:$GUID,port:1,button:1\"" "$INI"
  grep -qxF "profiles\\1\\button_b=\"engine:sdl,api:controller,guid:$GUID,port:1,button:0\"" "$INI"
  grep -qxF "profiles\\1\\circle_pad=\"engine:sdl,api:controller,guid:$GUID,port:1,axis_x:0,axis_y:1,deadzone:0.1\"" "$INI"
  grep -qxF "profiles\\1\\button_zr=\"engine:sdl,api:controller,guid:$GUID,port:1,axis:5,direction:+,threshold:0.5\"" "$INI"
  ! grep -qF "$OTHER" "$INI"
}

@test "the old bindings and their default markers go, the rest of the file stays" {
  mkdir -p "${INI%/*}"
  cat >"$INI" <<'INI'
[Audio]
volume=1
[Controls]
profile=0
profile\default=true
profiles\1\button_a="code:65,engine:keyboard"
profiles\1\button_a\default=true
profiles\1\name=Default
[Layout]
layout_option=2
INI
  pads_are "[$(pad Pad "$GUID" 0 false)]"
  pads_azahar_configure env-3ds

  ! grep -qF "engine:keyboard" "$INI"
  ! grep -qF 'button_a\default' "$INI"
  ! grep -qF 'profile\default' "$INI"
  [ "$(grep -c '^profiles\\1\\button_a=' "$INI")" = 1 ]
  grep -qxF 'profiles\1\name=Default' "$INI"
  grep -qxF 'volume=1' "$INI"
  # Written inside [Controls], before the section after it.
  [ "$(awk '/^\[Layout\]/{exit} /button_a=/{print "in"}' "$INI")" = in ]
}

@test "a pad with a gyro drives motion; one without keeps the default" {
  pads_are "[$(pad Pad "$GUID" 0 true)]"
  pads_azahar_configure env-3ds
  grep -qxF "profiles\\1\\motion_device=\"engine:sdl,guid:$GUID,port:0\"" "$INI"

  rm -f "$INI"
  pads_are "[$(pad Pad "$GUID" 0 false)]"
  pads_azahar_configure env-3ds
  ! grep -qF motion_device "$INI"
}

@test "a second launch rewrites nothing" {
  pads_are "[$(pad Pad "$GUID" 0 false)]"
  pads_azahar_configure env-3ds
  cp "$INI" "$TEST_TMP/once"
  pads_azahar_configure env-3ds
  diff "$TEST_TMP/once" "$INI"
}

@test "no controller attached writes nothing" {
  pads_are "[]"
  run pads_azahar_configure env-3ds
  [ "$status" -eq 0 ]
  [ ! -e "$INI" ]
}
