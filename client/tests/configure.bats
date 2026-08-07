#!/usr/bin/env bats
# `gotg configure` — resolving which environment's settings to open.
#
# The launch itself is a GUI and cannot be tested here, so what these cover is
# everything up to the exec: which environment a name resolves to, and refusing
# clearly rather than opening the wrong one.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
  # A SNES game, so both env-snes and its per-game environment are reachable.
  add_game snes world.super_metroid.sfc "rom" "Super Metroid"
}

teardown() { stop_server; }

# A built environment that declares it can be configured.
fake_configurable_root() {
  local attr="$1" exe="${2:-}"
  mkdir -p "$GOTG_ROOTS_DIR/$attr/share/gotg" "$GOTG_ROOTS_DIR/$attr/bin"
  if [[ -z "$exe" ]]; then
    exe="$GOTG_ROOTS_DIR/$attr/bin/emulator"
    printf '#!/bin/sh\necho "emulator opened"\necho "config=$XDG_CONFIG_HOME"\necho "hint=${SDL_JOYSTICK_HIDAPI_STEAM:-unset}"\n' >"$exe"
    chmod +x "$exe"
  fi
  jq -n --arg e "$exe" '{exec: $e, env: {SDL_JOYSTICK_HIDAPI_STEAM: "1"}}' \
    >"$GOTG_ROOTS_DIR/$attr/share/gotg/configure.json"
}

@test "it opens the environment's own emulator, pointed at its own settings" {
  fake_configurable_root env-snes-world_super_metroid
  gotg configure world.super_metroid
  [ "$status" -eq 0 ]
  [[ "$output" == *"emulator opened"* ]]
  # The whole point: the emulator sees this environment's config directory and
  # not the player's own.
  [[ "$output" == *"config=$GOTG_STATE_DIR/env/env-snes-world_super_metroid/config"* ]]
}

@test "a game's own environment is opened in preference to its platform's" {
  # Both are built, and this game has an env file of its own, so that is the
  # one whose settings a person means.
  fake_configurable_root env-snes
  fake_configurable_root env-snes-world_super_metroid
  gotg configure world.super_metroid
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-snes-world_super_metroid/config"* ]]
  [[ "$output" != *"env-snes/config"* ]]
}

@test "an environment that is not built says so, and what to run" {
  gotg configure world.super_metroid
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not built here yet"* ]]
  [[ "$stderr" == *"gotg install"* ]]
}

@test "an environment with no settings screen is refused rather than launched" {
  # A Harkinian port: starting it with no arguments starts the game, which is
  # not what configure means.
  mkdir -p "$GOTG_ROOTS_DIR/env-snes-world_super_metroid/share/gotg"
  gotg configure world.super_metroid
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no settings screen"* ]]
}

@test "a missing emulator is reported rather than exec'd" {
  fake_configurable_root env-snes-world_super_metroid "/nonexistent/emulator"
  gotg configure world.super_metroid
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"missing"* ]]
}

@test "it needs a game" {
  gotg configure
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg configure"* ]]
}

@test "an unknown variant is refused before anything is opened" {
  fake_configurable_root env-snes-world_super_metroid
  gotg configure world.super_metroid nosuchvariant
  [ "$status" -ne 0 ]
  [[ "$output" != *"emulator opened"* ]]
}

@test "it runs under the same environment a launch would" {
  # The SDL hints decide whether a controller exists at all — Ryujinx's SDL2
  # sees no Steam Controller without this one. A settings screen that lacked
  # them would be binding a pad the game will not have.
  fake_configurable_root env-snes-world_super_metroid
  gotg configure world.super_metroid
  [ "$status" -eq 0 ]
  [[ "$output" == *"hint=1"* ]]
}

@test "an environment that sets no variables still opens" {
  mkdir -p "$GOTG_ROOTS_DIR/env-snes-world_super_metroid/share/gotg" \
    "$GOTG_ROOTS_DIR/env-snes-world_super_metroid/bin"
  local exe="$GOTG_ROOTS_DIR/env-snes-world_super_metroid/bin/emulator"
  printf '#!/bin/sh\necho "emulator opened"\n' >"$exe"
  chmod +x "$exe"
  jq -n --arg e "$exe" '{exec: $e}' \
    >"$GOTG_ROOTS_DIR/env-snes-world_super_metroid/share/gotg/configure.json"
  gotg configure world.super_metroid
  [ "$status" -eq 0 ]
  [[ "$output" == *"emulator opened"* ]]
}
