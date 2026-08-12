#!/usr/bin/env bats
# --help on every command.
#
# A command that takes an id or an argument should be able to say what it takes
# without being made to fail first — so -h and --help print a usage and exit 0,
# for the leaf commands here and (already, in their own files) for the groups.

bats_require_minimum_version 1.5.0

load helper

setup() { setup_env; }

@test "every leaf command answers --help without needing the server" {
  local cmd
  for cmd in login refresh list info download install play configure sync; do
    gotg "$cmd" --help
    [ "$status" -eq 0 ] || {
      echo "gotg $cmd --help exited $status" >&2
      false
    }
    [[ "$output" == *"usage: gotg $cmd"* ]] || {
      echo "gotg $cmd --help did not name itself: $output" >&2
      false
    }
  done
}

@test "-h is the same as --help" {
  gotg install -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage: gotg install"* ]]
}

@test "list --help documents the search and page flags" {
  gotg list --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--search"* ]]
  [[ "$output" == *"--platform"* ]]
  [[ "$output" == *"--page"* ]]
}

@test "an alias answers help under its canonical name" {
  gotg ls --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage: gotg list"* ]]
}

@test "the group commands still print their own subcommand help" {
  local cmd
  for cmd in steam saves controllers; do
    gotg "$cmd" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"gotg $cmd"* ]]
  done
}

@test "help does not fetch or need a token" {
  rm -f "$GOTG_CONFIG_DIR/api.json"
  gotg list --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage: gotg list"* ]]
}
