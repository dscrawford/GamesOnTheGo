#!/usr/bin/env bats
# nix's own progress, as lines the picker can draw.
#
# An environment build run for the picker printed nothing for as long as nix
# evaluated, fetched and compiled -- a loader with a clock on it, and no way
# to tell a minute of evaluation from a stuck one. nix says what it is doing
# when asked for `--log-format internal-json`; these check that it is asked,
# and that what it says becomes `stage` lines.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

# A few lines as nix 2.3x writes them, trimmed: an evaluation, one path
# fetched from the cache, one derivation built, and a line of its build log.
nix_json() {
  cat <<'JSON'
@nix {"action":"msg","level":4,"msg":"evaluating file '/flake/flake.nix'"}
@nix {"action":"start","id":1,"level":4,"parent":0,"text":"evaluating derivation 'git+file:///flake#env-n64'","type":0}
@nix {"action":"stop","id":1}
@nix {"action":"start","id":2,"level":0,"parent":0,"text":"","type":104}
@nix {"action":"start","id":3,"level":0,"parent":0,"text":"","type":103}
@nix {"action":"result","fields":[0,3,0,0],"id":3,"type":105}
@nix {"action":"start","fields":["/nix/store/mxf46pw5223bwaini3wdjk3b3dn8qhgf-mesa-26.1","https://cache.nixos.org"],"id":4,"level":0,"parent":0,"text":"","type":108}
@nix {"action":"result","fields":[1,3,0,0],"id":3,"type":105}
@nix {"action":"result","fields":[1,3,0,0],"id":3,"type":105}
@nix {"action":"result","fields":[0,1,0,0],"id":2,"type":105}
@nix {"action":"start","id":5,"level":3,"parent":0,"text":"building '/nix/store/b0vl7vpakxfkszsvakjysb51w60sw8g8-ares-148.drv'","type":105}
@nix {"action":"result","fields":["compiling ares.cpp"],"id":5,"type":101}
@nix {"action":"result","fields":[1,1,0,0],"id":2,"type":105}
JSON
}

@test "evaluation is a stage, named after what is being evaluated" {
  run nix_stages < <(nix_json)
  [ "$status" -eq 0 ]
  [[ "$output" == *$'stage\teval\t1\t0\t'* ]]
  [[ "$output" == *$'stage\teval\t1\t0\tenv-n64'* ]]
}

@test "fetching and building are counted stages, with the path they are on" {
  run nix_stages < <(nix_json)
  [[ "$output" == *$'stage\tfetch\t0\t3\t'* ]]
  [[ "$output" == *$'stage\tfetch\t1\t3\tmesa-26.1'* ]]
  [[ "$output" == *$'stage\tbuild\t0\t1\t'* ]]
  [[ "$output" == *$'stage\tbuild\t1\t1\tares-148'* ]]
}

@test "a figure that did not change is said once" {
  run nix_stages < <(nix_json)
  [ "$(grep -c $'^stage\tfetch\t1\t3' <<<"$output")" -eq 1 ]
}

@test "a build's own output and nix's errors pass through as text" {
  run nix_stages < <(
    nix_json
    printf '%s\n' '@nix {"action":"msg","level":0,"msg":"\u001b[31;1merror:\u001b[0m builder for ares failed"}'
    printf 'a line nix wrote without the prefix\n'
  )
  [[ "$output" == *"compiling ares.cpp"* ]]
  [[ "$output" == *"error: builder for ares failed"* ]]
  [[ "$output" == *"a line nix wrote without the prefix"* ]]
}

@test "a line that is not json does not stop the stream" {
  run nix_stages < <(printf '@nix {broken\n'; nix_json)
  [ "$status" -eq 0 ]
  [[ "$output" == *$'stage\tbuild\t1\t1'* ]]
}

# --- env_build asks for it only when somebody is drawing it ----------------

json_nix() {
  local mode="${1:-ok}"
  export NIX_LOG="$TEST_TMP/nix.log" GOTG_FLAKE="$TEST_TMP/flake" GOTG_NIX="$TEST_TMP/bin/nix"
  mkdir -p "$GOTG_FLAKE" "$TEST_TMP/bin"
  : >"$GOTG_FLAKE/flake.nix"
  nix_json >"$TEST_TMP/nix.json"
  cat >"$GOTG_NIX" <<SHIM
#!$(command -v bash)
printf '%s\n' "\$*" >>"$NIX_LOG"
[[ "\$*" != *internal-json* ]] || cat "$TEST_TMP/nix.json" >&2
[[ "$mode" == ok ]] || { echo "error: it broke" >&2; exit 1; }
prev=""; for arg in "\$@"; do [[ "\$prev" != -o ]] || out="\$arg"; prev="\$arg"; done
mkdir -p "\$out/bin"; printf '#!/bin/sh\n' >"\$out/bin/gotg-play"; chmod +x "\$out/bin/gotg-play"
SHIM
  chmod +x "$GOTG_NIX"
}

@test "a build for the picker asks nix for its progress and draws it" {
  json_nix
  GOTG_PROGRESS_LINES=1 GOTG_NO_DIALOG=1 run --separate-stderr env_build env-n64
  [ "$status" -eq 0 ]
  grep -q -- "--log-format internal-json" "$NIX_LOG"
  [[ "$stderr" == *$'stage\tbuild\t1\t1\tares-148'* ]]
  [[ "$stderr" != *"@nix"* ]]
}

@test "a build at a terminal is nix's own, untouched" {
  json_nix
  GOTG_NO_DIALOG=1 run --separate-stderr env_build env-n64
  [ "$status" -eq 0 ]
  ! grep -q -- "internal-json" "$NIX_LOG"
}

@test "a build that fails for the picker still fails, and says why" {
  json_nix fail
  GOTG_PROGRESS_LINES=1 GOTG_NO_DIALOG=1 run --separate-stderr env_build env-n64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"could not build env-n64"* ]]
}
