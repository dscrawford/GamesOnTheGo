#!/usr/bin/env bats
# common.sh's file writers: atomic_write and json_merge_file.
#
# atomic_write sets the mode before the secret goes in and leaves no `.tmp`
# behind when the writer fails; these pin both.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
}

mode_of() { stat -c %a "$1"; }

# Run as atomic_write's writer: records the mode of the file it is writing to
# before it has put a byte there, which is the whole point of the 0600 files.
peek_mode_then_write() {
  local f
  for f in "$TEST_TMP"/target.*; do stat -c %a "$f" >"$TEST_TMP/mode-while-writing"; done
  printf 'secret\n'
}

@test "atomic_write replaces the file with what its command printed" {
  printf 'old\n' >"$TEST_TMP/target"
  atomic_write "$TEST_TMP/target" "" -- printf 'new\n'
  [ "$(cat "$TEST_TMP/target")" = "new" ]
}

@test "atomic_write reads stdin when it is given no command" {
  printf 'from stdin\n' | atomic_write "$TEST_TMP/target"
  [ "$(cat "$TEST_TMP/target")" = "from stdin" ]
}

@test "atomic_write sets the mode before the first byte is written" {
  atomic_write "$TEST_TMP/target" 600 -- peek_mode_then_write
  [ "$(cat "$TEST_TMP/mode-while-writing")" = "600" ]
  [ "$(mode_of "$TEST_TMP/target")" = "600" ]
}

@test "atomic_write with no mode leaves the file as the umask makes one" {
  umask 022
  atomic_write "$TEST_TMP/target" "" -- printf 'x'
  [ "$(mode_of "$TEST_TMP/target")" = "644" ]
}

@test "atomic_write that fails keeps the old file and leaves nothing beside it" {
  printf 'old\n' >"$TEST_TMP/target"
  run -1 atomic_write "$TEST_TMP/target" 600 -- bash -c 'printf partial; exit 1'
  [ "$(cat "$TEST_TMP/target")" = "old" ]
  [ -z "$(ls -A "$TEST_TMP" | grep '^target\.')" ]
}

@test "atomic_write that fails makes no file where there was none" {
  run -1 atomic_write "$TEST_TMP/target" -- false
  [ ! -e "$TEST_TMP/target" ]
}

@test "json_merge_file makes the file from the patch when there is none, mode 600" {
  json_merge_file "$TEST_TMP/conf.json" '{"url":"http://x"}' 600
  [ "$(jq -c . "$TEST_TMP/conf.json")" = '{"url":"http://x"}' ]
  [ "$(mode_of "$TEST_TMP/conf.json")" = "600" ]
}

@test "json_merge_file keeps what the file had and the patch overrides" {
  printf '{"a":1,"b":2}' >"$TEST_TMP/conf.json"
  json_merge_file "$TEST_TMP/conf.json" '{"b":3,"c":4}' 600
  [ "$(jq -c . "$TEST_TMP/conf.json")" = '{"a":1,"b":3,"c":4}' ]
}

@test "json_merge_file on a file that is not JSON leaves it as it was" {
  printf 'not json' >"$TEST_TMP/conf.json"
  run -1 json_merge_file "$TEST_TMP/conf.json" '{"a":1}' 600
  [ "$(cat "$TEST_TMP/conf.json")" = "not json" ]
  [ -z "$(ls -A "$TEST_TMP" | grep '^conf\.json\.')" ]
}

@test "json_merge_file refuses a patch that is not JSON" {
  run -1 json_merge_file "$TEST_TMP/conf.json" 'nope' 600
  [ ! -e "$TEST_TMP/conf.json" ]
}

# Held by someone else for a few seconds, standing in for another gotg.
hold_lock() {
  flock "$1" sleep "$2" &
  HOLDER=$!
  # The holder has the lock once flock -n from here is refused.
  local i
  for i in $(seq 1 50); do
    flock -n "$1" true 2>/dev/null || return 0
    sleep 0.1
  done
  return 1
}

@test "with_lock runs the command and hands back its status" {
  ran() { printf 'ran\n' >"$TEST_TMP/ran"; return 3; }
  run -3 with_lock "$TEST_TMP/l" "" "never" ran
  [ -e "$TEST_TMP/ran" ]
}

@test "with_lock lets go of the lock when the command is done" {
  noop() { :; }
  with_lock "$TEST_TMP/l" "" "never" noop
  flock -n "$TEST_TMP/l" true
}

@test "with_lock lets go of the lock when the command fails" {
  boom() { return 1; }
  run -1 with_lock "$TEST_TMP/l" "" "never" boom
  flock -n "$TEST_TMP/l" true
}

@test "with_lock gives up with the caller's message when the lock stays taken" {
  hold_lock "$TEST_TMP/l" 5
  never() { printf 'ran\n' >"$TEST_TMP/ran"; }
  run -1 --separate-stderr with_lock "$TEST_TMP/l" 1 "another gotg has it" never
  kill "$HOLDER" 2>/dev/null || true
  [[ "$stderr" == *"another gotg has it"* ]]
  [ ! -e "$TEST_TMP/ran" ]
}

@test "with_lock makes a second caller wait for the first to finish" {
  slow() { printf 'a-in\n' >>"$TEST_TMP/order"; sleep 0.5; printf 'a-out\n' >>"$TEST_TMP/order"; }
  quick() { printf 'b-in\n' >>"$TEST_TMP/order"; printf 'b-out\n' >>"$TEST_TMP/order"; }
  with_lock "$TEST_TMP/l" "" "never" slow &
  local first=$!
  sleep 0.2
  with_lock "$TEST_TMP/l" 10 "never" quick
  wait "$first"
  [ "$(tr '\n' ' ' <"$TEST_TMP/order")" = "a-in a-out b-in b-out " ]
}

@test "with_lock keeps errexit on inside the command" {
  # A body run on the left of || would have errexit silently off, which is
  # what the lock's callers must not inherit.
  checked() { false; printf 'went on\n' >"$TEST_TMP/went-on"; }
  run -1 bash -c 'set -e; source "$GOTG_LIB/common.sh"; '"$(declare -f checked)"'; with_lock "$1" "" never checked' _ "$TEST_TMP/l"
  [ ! -e "$TEST_TMP/went-on" ]
}

@test "HOME is inside the test's own directory, never the real one" {
  [[ "$HOME" == "$TEST_TMP"/* ]]
  [ -d "$HOME" ]
}

@test "wait_for returns as soon as the condition holds" {
  (sleep 0.3; touch "$TEST_TMP/ready") &
  local before=$SECONDS
  wait_for 10 test -e "$TEST_TMP/ready"
  [ $((SECONDS - before)) -lt 5 ]
}

@test "wait_for gives up with the condition's own failure when time is up" {
  run -1 wait_for 1 test -e "$TEST_TMP/never"
}

@test "wait_for can poll a shell function" {
  later() { [[ -e "$TEST_TMP/flag" ]]; }
  (sleep 0.2; touch "$TEST_TMP/flag") &
  wait_for 5 later
}

@test "make_stub writes an executable script whose body is its arguments" {
  make_stub "$TEST_TMP/deep/dir/tool" 'echo "args: $*"' 'exit 3'
  [ -x "$TEST_TMP/deep/dir/tool" ]
  run -3 "$TEST_TMP/deep/dir/tool" a b
  [ "$output" = "args: a b" ]
}
