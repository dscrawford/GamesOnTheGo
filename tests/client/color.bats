#!/usr/bin/env bats
# When there is colour and when there is not.
#
# The rules matter more than the palette: colour leaking into a pipe corrupts
# whatever reads it, and these tests run with both streams captured, which is
# the same condition Steam launches under.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

# An escape byte anywhere in the output.
has_escapes() { [[ "$1" == *$'\033'* ]]; }

@test "no colour when the output is not a terminal" {
  unset NO_COLOR FORCE_COLOR
  color_init
  run warn "something"
  has_escapes "$output" && return 1
  [ "$output" = "warning: something" ]
}

@test "FORCE_COLOR turns it on even with no terminal" {
  # bats runs with TERM=dumb, which is itself a reason to refuse — so a test
  # for the FORCE_COLOR rule has to name a terminal that can render it.
  TERM=xterm FORCE_COLOR=1 color_init
  run warn "something"
  has_escapes "$output" || return 1
  # The label is coloured and the message is not, so quoting or grepping the
  # text still gets the text.
  [[ "$output" == *"warning:"*"something" ]]
}

@test "NO_COLOR wins over FORCE_COLOR" {
  NO_COLOR=1 FORCE_COLOR=1 color_init
  run warn "something"
  has_escapes "$output" && return 1
  [ "$output" = "warning: something" ]
}

@test "NO_COLOR counts however it is set" {
  # The convention is presence, not truthiness: NO_COLOR=0 still means no.
  NO_COLOR=0 color_init
  run color_enabled
  [ "$status" -ne 0 ]
}

@test "a terminal that says it cannot render this is believed" {
  TERM=dumb FORCE_COLOR=1 color_init
  run color_enabled
  [ "$status" -ne 0 ]
}

@test "every name is defined either way, so call sites never test a flag" {
  color_init
  # Set-but-empty, not unset: `set -u` is on everywhere these are used.
  for v in C_RESET C_BOLD C_DIM C_RED C_GREEN C_YELLOW C_CYAN \
    C_ERROR C_WARN C_OK C_ID C_HEAD C_MUTED; do
    [[ -n "${!v+set}" ]] || {
      echo "$v is unset"
      return 1
    }
    [ -z "${!v}" ]
  done
}

@test "error and warning are told apart by more than colour" {
  # Whoever cannot see the colour still gets the word.
  color_init
  run warn "careful"
  [[ "$output" == "warning: "* ]]
  run die "broken"
  [ "$status" -eq 1 ]
  [[ "$output" == "error: "* ]]
}

@test "colour never reaches a machine-readable stream" {
  # `controllers order --json` is parsed by a UI. jq output carries no palette
  # even when colour is on, because nothing puts one there.
  TERM=xterm FORCE_COLOR=1 color_init
  run jq -nc '{a: 1}'
  has_escapes "$output" && return 1
  [ "$output" = '{"a":1}' ]
}
