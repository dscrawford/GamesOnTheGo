#!/usr/bin/env bats
# The controller's way out of a running game.
#
# The combination itself — both shoulders and Start, held for three seconds —
# is decided in C and tested on a clock the test owns (killswitch_test.c, run
# by the killswitch flake check). What is tested here is the wiring: that every
# launch starts the watcher, that it is told the pid that becomes the emulator,
# and that a machine without one still plays games.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config

  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64

  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"

  export WATCHER_LOG="$TEST_TMP/watcher.args"
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
}

teardown() {
  stop_saves_service
}

# A stand-in watcher: records how it was called and leaves. The real one is
# SDL, a controller and three seconds, none of which a build sandbox has.
fake_watcher() {
  local script="$TEST_TMP/fake-killswitch"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "%%s\\n" "$*" >>"%s"\n' "$WATCHER_LOG"
    printf '%s\n' "${1:-exit 0}"
  } >"$script"
  chmod +x "$script"
  export GOTG_KILLSWITCH_BIN="$script"
}

# The watcher is spawned in the background and the shell it was spawned from
# execs immediately, so its first write races the assertion.
wait_for_watcher() {
  local i
  for i in $(seq 1 50); do
    [[ -s "$WATCHER_LOG" ]] && return 0
    sleep 0.1
  done
  return 1
}

@test "every launch starts the watcher" {
  fake_watcher
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  wait_for_watcher
  [[ "$(cat "$WATCHER_LOG")" == *"--pid"* ]]
}

@test "the watcher is told the pid that becomes the emulator" {
  # gotg play execs the environment's wrapper, so the pid handed over is the
  # one the emulator ends up with. A watcher pointed at anything else would
  # signal a process that no longer exists.
  fake_watcher
  gotg play usa.zelda
  wait_for_watcher
  local pid
  pid="$(sed -n 's/.*--pid \([0-9]*\).*/\1/p' "$WATCHER_LOG")"
  [ -n "$pid" ]
  [ "$pid" -gt 0 ]
}

@test "it holds the combo for three seconds by default" {
  fake_watcher
  gotg play usa.zelda
  wait_for_watcher
  [[ "$(cat "$WATCHER_LOG")" == *"--hold-ms 3000"* ]]
}

@test "the hold can be retuned without touching the launch" {
  fake_watcher
  GOTG_KILLSWITCH_HOLD_MS=5000 gotg play usa.zelda
  wait_for_watcher
  [[ "$(cat "$WATCHER_LOG")" == *"--hold-ms 5000"* ]]
}

@test "a hold time that is not a number falls back rather than disarming" {
  # The watcher refuses an unparseable --hold-ms and exits, which would leave a
  # launch that announced a kill switch it does not have.
  fake_watcher
  GOTG_KILLSWITCH_HOLD_MS="banana" gotg play usa.zelda
  wait_for_watcher
  [[ "$(cat "$WATCHER_LOG")" == *"--hold-ms 3000"* ]]
  [[ "$stderr" == *"not a number of milliseconds"* ]]
}

@test "a hold time cannot smuggle a command into the launch" {
  # $(( )) evaluates array subscripts, and an array subscript runs command
  # substitution — so this reaches arithmetic only after it has been checked.
  fake_watcher
  GOTG_KILLSWITCH_HOLD_MS="x[\$(touch $TEST_TMP/ran-a-command)]" gotg play usa.zelda
  wait_for_watcher
  [ ! -e "$TEST_TMP/ran-a-command" ]
  [[ "$(cat "$WATCHER_LOG")" == *"--hold-ms 3000"* ]]
}

@test "the launch says how to use it" {
  fake_watcher
  gotg play usa.zelda
  [[ "$stderr" == *"hold L + R and Start for 3s"* ]]
}

@test "GOTG_KILLSWITCH=0 turns it off and the game still runs" {
  fake_watcher
  GOTG_KILLSWITCH=0 gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64 launched with"* ]]
  [ ! -e "$WATCHER_LOG" ]
}

@test "a machine with no watcher still plays the game, and says so" {
  GOTG_KILLSWITCH_BIN="$TEST_TMP/not-here" gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64 launched with"* ]]
  [[ "$stderr" == *"cannot stop this game"* ]]
}

@test "a watcher that dies on startup does not take the game with it" {
  fake_watcher 'exit 3'
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64 launched with"* ]]
}

# --- the watcher itself ------------------------------------------------------
#
# The combination is decided in C against a fake clock; what is left to check
# against the real binary is what it does to processes, which no unit test of
# the logic can reach.

# `skip` cannot work from inside $( ): it would run in a subshell and take the
# subshell with it, leaving the test running against an empty command name.
# So the lookup happens once, and each test checks it.
have_killswitch() {
  KS="$(command -v gotg-killswitch 2>/dev/null || true)"
  [[ -n "$KS" ]] || skip "gotg-killswitch is not on PATH"
}

@test "it says what the combination is" {
  have_killswitch
  run "$KS" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"shoulders"* ]]
  [[ "$output" == *"Start"* ]]
}

@test "it refuses to run without something to watch" {
  have_killswitch
  run "$KS"
  [ "$status" -eq 2 ]
  [[ "$output" == *"--pid"* ]]
}

@test "a pid that is not a number is refused rather than guessed at" {
  have_killswitch
  run "$KS" --pid banana
  [ "$status" -eq 2 ]
  run "$KS" --pid 0
  [ "$status" -eq 2 ]
  run "$KS" --hold-ms
  [ "$status" -eq 2 ]
}

@test "a negative hold is refused, not turned into forever" {
  # strtoull takes a leading minus and hands back the wraparound, so without
  # an explicit check this is a hold of 584 million years: a launch that
  # announces a kill switch which can never fire.
  have_killswitch
  run "$KS" --hold-ms -1 --pid "$$"
  [ "$status" -eq 2 ]
}

@test "it will not be pointed at init" {
  # A group kill of pid 1 is kill(-1), which is "every process this user may
  # signal" — a container entrypoint away from logging somebody out.
  have_killswitch
  run "$KS" --pid 1
  [ "$status" -eq 2 ]
}

@test "watching a game that has already exited is not an error" {
  # The race every launch has: the watcher starts, the game was already gone.
  # Nothing to guard is the outcome it exists to produce, not a failure.
  have_killswitch
  bash -c 'exit 0' &
  local gone=$!
  wait "$gone" || true
  run "$KS" --pid "$gone" --poll-ms 50
  [ "$status" -eq 0 ]
}

@test "a game that has ended but not been reaped counts as gone" {
  # kill(pid, 0) answers a zombie exactly like a healthy process, so without
  # the /proc check the watcher would sit here guarding something that has
  # already exited — and, on firing, signal a corpse.
  have_killswitch
  bash -c "sleep 0 & echo \$! >'$TEST_TMP/zombie.pid'; sleep 30" &
  local holder=$! zombie="" i
  for i in $(seq 1 50); do
    [[ -s "$TEST_TMP/zombie.pid" ]] && break
    sleep 0.1
  done
  zombie="$(cat "$TEST_TMP/zombie.pid")"
  for i in $(seq 1 50); do
    [[ "$(ps -o stat= -p "$zombie" 2>/dev/null)" == Z* ]] && break
    sleep 0.1
  done
  [[ "$(ps -o stat= -p "$zombie" 2>/dev/null)" == Z* ]] || skip "could not make a zombie here"

  run timeout 5 "$KS" --pid "$zombie" --poll-ms 50 --quiet
  [ "$status" -eq 0 ]

  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
}

@test "signalling the watcher leaves the game alone" {
  # Steam and systemd both send SIGTERM to the helpers around a session as it
  # tears down. The watcher must quit on that — and quit without going through
  # the thing that stops games, or its own shutdown becomes a kill switch.
  have_killswitch
  sleep 30 &
  local game=$!
  "$KS" --pid "$game" --poll-ms 50 --quiet &
  local watcher=$!
  sleep 0.5

  kill -TERM "$watcher"
  local i
  for i in $(seq 1 50); do
    kill -0 "$watcher" 2>/dev/null || break
    sleep 0.1
  done
  run ! kill -0 "$watcher"
  kill -0 "$game" || {
    echo "the watcher took the game with it" >&2
    false
  }

  kill "$game" 2>/dev/null || true
  wait "$game" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
}

@test "it lets go when the game ends on its own" {
  # No controller here, so this is the ordinary ending: the emulator exits and
  # the watcher must not be left behind holding a dead pid.
  have_killswitch
  sleep 1 &
  local game=$!
  run timeout 20 "$KS" --pid "$game" --poll-ms 50 --quiet
  wait "$game" || true
  [ "$status" -eq 0 ]
}

@test "the real watcher takes what the launch sends it" {
  # The stub above proves the launch survives a watcher that dies; this proves
  # the arguments the launch actually sends are ones the real watcher accepts,
  # which no stub can tell us.
  have_killswitch
  GOTG_KILLSWITCH_BIN="$KS" GOTG_KILLSWITCH_HOLD_MS=banana gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64 launched with"* ]]
  [[ "$stderr" == *"not a number of milliseconds"* ]]
}

@test "the combination really stops a real game on a real pad" {
  # The only test that proves the three halves together: SDL seeing a pad, the
  # combination as a controller actually reports it, and the signal landing on
  # the game. Needs a virtual controller, so it skips where one cannot be made
  # — which includes the build sandbox this normally runs in.
  have_killswitch
  python3 -c 'import evdev' 2>/dev/null || skip "python-evdev is not here"
  [[ -w /dev/uinput ]] || skip "/dev/uinput is not writable here"

  run timeout 120 python3 "$BATS_TEST_DIRNAME/killswitch_e2e.py" "$KS"
  [ "$status" -eq 0 ]
  [[ "$output" != *"not ok"* ]]
}
