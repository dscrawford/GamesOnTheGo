#!/usr/bin/env bats
# The GL a machine with no /run/opengl-driver needs, fetched when it is
# needed rather than carried by every environment.
#
# SteamOS has no /run/opengl-driver, and its own mesa will not load into a
# program built against nixpkgs' glibc, so on a Deck every environment points
# GL, EGL, GBM and Vulkan at nixpkgs' mesa (env/foreign-gl.nix). That mesa
# used to be in every environment's closure -- a gigabyte with its LLVM, on
# NixOS too, where it is never loaded. Now an environment only names it, and
# the client fetches exactly the one named, before the game, where it is
# needed, and keeps a root to it so a garbage collection cannot take it.

bats_require_minimum_version 1.5.0

load helper

MESA=/nix/store/0123456789abcdfghijklmnpqrsvwxyz-mesa-26.2.3
OTHER=/nix/store/zyxwvsrqpnmlkjihgfdcba9876543210-mesa-26.1.5

setup() {
  setup_env
  start_saves_service
  write_api_config

  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  printf '%s\n' "$MESA" >"$GOTG_ROOTS_DIR/env-n64/share/gotg/foreign-gl"

  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"
  export GOTG_KILLSWITCH=0
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  stub_nix
  # The client's own -- the overlay's and the picker's -- is not what these
  # are about.
  export GOTG_FOREIGN_GL_SELF=none
  use_library
}

teardown() {
  stop_saves_service
}

fetched() { grep -F -- "build $1 -o $GOTG_STATE_DIR/foreign-gl/$(basename "$1")" "$NIX_LOG"; }

@test "a machine with its own GL fetches nothing for it" {
  export GOTG_HOST_GL="$TEST_TMP"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  ! grep -q mesa "$NIX_LOG" 2>/dev/null
}

@test "a machine without it fetches the one the environment was built with, and keeps it" {
  export GOTG_HOST_GL="$TEST_TMP/no-such-gl"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  fetched "$MESA"
  [ -e "$GOTG_STATE_DIR/foreign-gl/$(basename "$MESA")" ]
  [[ "$output" == *"launched with"* ]]
}

@test "GOTG_FOREIGN_GL=1 fetches it where the host has its own, as a Deck's run here does" {
  export GOTG_HOST_GL="$TEST_TMP" GOTG_FOREIGN_GL=1
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  fetched "$MESA"
}

@test "an environment from before, naming nothing, is left as it is" {
  export GOTG_HOST_GL="$TEST_TMP/no-such-gl"
  rm "$GOTG_ROOTS_DIR/env-n64/share/gotg/foreign-gl"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  ! grep -q mesa "$NIX_LOG" 2>/dev/null
}

@test "what it names is fetched only if it is a mesa in the store" {
  export GOTG_HOST_GL="$TEST_TMP/no-such-gl"
  local bad
  for bad in /etc/passwd "nixpkgs#mesa" "/nix/store/short-mesa-1" "$MESA/../../etc" "--option sandbox false"; do
    printf '%s\n' "$bad" >"$GOTG_ROOTS_DIR/env-n64/share/gotg/foreign-gl"
    gotg play usa.zelda
    [ "$status" -eq 0 ]
    ! grep -qF -- "$bad" "$NIX_LOG" 2>/dev/null
  done
}

@test "a fetch that fails says so, and the game still starts" {
  export GOTG_HOST_GL="$TEST_TMP/no-such-gl" NIX_FAIL_STORE_PATHS=1
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"could not fetch the GL"* ]]
  [[ "$output" == *"launched with"* ]]
}

@test "update keeps the GL something here names, and lets the rest go" {
  export GOTG_HOST_GL="$TEST_TMP/no-such-gl"
  mkdir -p "$GOTG_STATE_DIR/foreign-gl/$(basename "$MESA")" "$GOTG_STATE_DIR/foreign-gl/$(basename "$OTHER")"
  # The fake nix builds a root with no share/gotg; the environment names
  # its mesa once it is rebuilt, as a real build would.
  sed -i 's|^exit 0$|[[ "$out" != */roots/env-* ]] \|\| { mkdir -p "$out/share/gotg"; printf "%s\\n" "'"$MESA"'" >"$out/share/gotg/foreign-gl"; }\nexit 0|' "$GOTG_NIX"
  gotg update
  [ -e "$GOTG_STATE_DIR/foreign-gl/$(basename "$MESA")" ]
  [ ! -e "$GOTG_STATE_DIR/foreign-gl/$(basename "$OTHER")" ]
}

@test "on a machine with its own GL, update keeps none" {
  export GOTG_HOST_GL="$TEST_TMP"
  mkdir -p "$GOTG_STATE_DIR/foreign-gl/$(basename "$MESA")"
  gotg update
  [ ! -e "$GOTG_STATE_DIR/foreign-gl/$(basename "$MESA")" ]
}
