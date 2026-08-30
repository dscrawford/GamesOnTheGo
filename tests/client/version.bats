#!/usr/bin/env bats
# `gotg version` — which gotg this is.
#
# Small, and worth testing anyway for one reason: the version reaches the script
# through a build-time substitution, so it is the kind of thing that breaks
# without breaking anything else. A CLI reporting the literal "@version@" would
# pass every other test in this suite.

bats_require_minimum_version 1.5.0

load helper

setup() { setup_env; }

@test "version reports a real version, not the unsubstituted placeholder" {
  gotg version
  [ "$status" -eq 0 ]
  [[ "$output" != *"@version@"* ]]
  [[ "$output" != *"unbuilt checkout"* ]]
  [[ "${lines[0]}" =~ ^gotg\ [0-9]+\.[0-9]+ ]]
}

@test "the flags and the subcommand agree" {
  gotg version
  local plain="$output"
  gotg --version
  [ "$status" -eq 0 ]
  [ "$output" = "$plain" ]
  gotg -V
  [ "$status" -eq 0 ]
  [ "$output" = "$plain" ]
}

@test "it names where this gotg was installed from" {
  # The number is the less useful half here — every build of an unchanged tree
  # reports the same one — so the path is what answers "which gotg is on PATH".
  gotg version
  [ "${#lines[@]}" -eq 2 ]
  [ -d "${lines[1]}" ]
  [ -x "${lines[1]}/bin/gotg" ]
}

@test "version needs no network, no config and no catalog" {
  # It is what somebody runs when nothing else works, so it must not depend on
  # any of the things that might be why.
  rm -rf "$GOTG_CONFIG_DIR" "$GOTG_STATE_DIR"
  gotg version
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
}

@test "the usage lists it" {
  gotg help
  [ "$status" -eq 0 ]
  [[ "$output" == *"version"* ]]
}
