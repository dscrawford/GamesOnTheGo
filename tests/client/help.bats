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

@test "each alias answers help under its canonical name" {
  local pair alias canon
  for pair in "ls:list" "show:info" "get:download" "launch:play"; do
    alias="${pair%%:*}"
    canon="${pair##*:}"
    gotg "$alias" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"usage: gotg $canon"* ]] || {
      echo "gotg $alias --help did not map to '$canon': $output" >&2
      false
    }
  done
}

@test "help is only intercepted in the first position, not after an argument" {
  # An id can never be --help, so --help after a positional is a plain
  # unknown option — a clean failure, not a help screen.
  gotg list zelda --help
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"unknown option for list"* ]]
}

@test "complete --help is a harmless no-op, not a help screen" {
  gotg complete --help
  [ "$status" -eq 0 ]
  [ -z "$output" ]
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
