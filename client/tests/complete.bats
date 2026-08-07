#!/usr/bin/env bats
# `gotg complete` — the lists shell completion asks for.
#
# These run on a keypress, so the two properties that matter most are that they
# never reach the network and never fail loudly: a tab that blocks, or that
# prints an error over a half-typed command, is worse than no completion.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
}

teardown() { stop_server; }

@test "ids come from the cached catalog" {
  add_game snes world.super_metroid.sfc "rom" "Super Metroid"
  add_game n64 usa.legend_of_zelda_majoras_mask.z64 "rom" "Majora's Mask"
  gotg refresh
  gotg complete ids
  [ "$status" -eq 0 ]
  [[ "$output" == *"world.super_metroid"* ]]
  [[ "$output" == *"usa.legend_of_zelda_majoras_mask"* ]]
  # The id, not the filename: the extension is what distinguishes them.
  [[ "$output" != *".sfc"* ]]
}

@test "with no catalog it completes nothing, quietly" {
  # The first tab on a fresh machine must not look like a broken install.
  gotg complete ids
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "it never fetches — a dead server still completes from cache" {
  add_game snes world.super_metroid.sfc "rom" "Super Metroid"
  gotg refresh
  stop_server
  gotg complete ids
  [ "$status" -eq 0 ]
  [[ "$output" == *"world.super_metroid"* ]]
}

@test "platforms are offered too, deduplicated" {
  add_game snes world.super_metroid.sfc "rom" "Super Metroid"
  add_game snes world.other.sfc "rom" "Other"
  add_game n64 usa.zelda.z64 "rom" "Zelda"
  gotg refresh
  gotg complete platforms
  [ "$(grep -c snes <<<"$output")" -eq 1 ]
  [[ "$output" == *"n64"* ]]
}

@test "variants are the ones that exist for that game" {
  add_game gamecube usa.super_mario_sunshine.rvz "iso" "Super Mario Sunshine"
  gotg refresh
  gotg complete variants usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  # Both Sunshine variants ship in the package.
  [[ "$output" == *"bse"* ]]
  [[ "$output" == *"bsmso"* ]]
}

@test "a game with no variants completes nothing" {
  add_game snes world.super_metroid.sfc "rom" "Super Metroid"
  gotg refresh
  gotg complete variants world.super_metroid
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a half-typed or unknown id is not an error" {
  add_game snes world.super_metroid.sfc "rom" "Super Metroid"
  gotg refresh
  gotg complete variants world.super_met
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  gotg complete variants ""
  [ "$status" -eq 0 ]
}

@test "an unknown list is not an error either" {
  gotg complete nonsense
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "the completion script is shipped where bash looks for it" {
  local root
  root="$(cd "$(dirname "$GOTG_BIN")/.." && pwd)"
  [ -f "$root/share/bash-completion/completions/gotg" ]
  grep -q "complete -F _gotg gotg" "$root/share/bash-completion/completions/gotg"
}
