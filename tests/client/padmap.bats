#!/usr/bin/env bats
# A game started by gotg runs under padmap.
#
# Two things have to be true and neither may stop a launch: the daemon is
# asked for once, and the emulator is exec'd through padmap so SDL is told
# about the pads before it reads its database -- which it does once, at
# startup.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  load_client_libs
  source "$GOTG_LIB/padmap.sh"

  # Stand-ins that record how they were called rather than touching uinput.
  export FAKE_BIN="$TEST_TMP/bin"
  mkdir -p "$FAKE_BIN"
  export PADMAP_LOG="$TEST_TMP/padmap.log"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "padmap %%s\\n" "$*" >>"$PADMAP_LOG"\n'
    printf 'exit "${FAKE_PADMAP_EXIT:-0}"\n'
  } >"$FAKE_BIN/padmap"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "padmap-rs %%s\\n" "$*" >>"$PADMAP_LOG"\n'
    printf 'shift 2\n'
    printf 'exec "$@"\n'
  } >"$FAKE_BIN/padmap-rs"
  export SEAT_LOG="$TEST_TMP/seat.log"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "gotg-seat %%s\\n" "$*" >>"$SEAT_LOG"\n'
    printf 'exit "${FAKE_SEAT_EXIT:-0}"\n'
  } >"$FAKE_BIN/gotg-seat"
  chmod +x "$FAKE_BIN/padmap" "$FAKE_BIN/padmap-rs" "$FAKE_BIN/gotg-seat"
  export PATH="$FAKE_BIN:$PATH"
  # Named rather than found: the packaged client puts the real padmap first on
  # PATH, where a stand-in in this directory could never win.
  export GOTG_PADMAP="$FAKE_BIN/padmap"
  export GOTG_PADMAP_RS="$FAKE_BIN/padmap-rs"
  export GOTG_SEAT="$FAKE_BIN/gotg-seat"
  unset PADMAP_SKIP_DAEMON_CHECK
}

teardown() { stop_saves_service; }

@test "the daemon is asked for before a launch" {
  run padmap_ensure
  [ "$status" -eq 0 ]
  grep -q "padmap ensure-daemon" "$PADMAP_LOG"
}

@test "it is asked for once, not once per launch" {
  padmap_ensure
  padmap_ensure
  [ "$(grep -c "ensure-daemon" "$PADMAP_LOG")" = 1 ]
}

@test "a daemon that will not start does not stop the game" {
  # A machine where padmap cannot reach uinput still plays games, with
  # whatever SDL finds by itself.
  FAKE_PADMAP_EXIT=1 run --separate-stderr padmap_ensure
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"whatever SDL finds"* ]]
}

@test "the game is exec'd through padmap-rs" {
  # SDL reads its controller database once, at startup, so the mapping has to
  # be in the environment before the emulator is -- not written a second later.
  run bash -c 'source "$GOTG_LIB/common.sh"; source "$GOTG_LIB/padmap.sh"; padmap_exec echo played'
  [ "$status" -eq 0 ]
  [[ "$output" == *"played"* ]]
  grep -q "padmap-rs exec --" "$PADMAP_LOG"
}

@test "no padmap installed still launches the game" {
  rm -f "$FAKE_BIN/padmap" "$FAKE_BIN/padmap-rs"
  export GOTG_PADMAP="$FAKE_BIN/padmap" GOTG_PADMAP_RS="$FAKE_BIN/padmap-rs"
  run bash -c 'source "$GOTG_LIB/common.sh"; source "$GOTG_LIB/padmap.sh"; padmap_exec echo played'
  [ "$status" -eq 0 ]
  [[ "$output" == *"played"* ]]
}

@test "play runs the emulator under padmap" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "emulator ran"\n'
  } >"$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  chmod +x "$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"

  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"emulator ran"* ]]
  grep -q "padmap-rs exec --" "$PADMAP_LOG"
}

@test "the controller check runs before the game, and is told which console" {
  # The console is what decides the control set the capture walks, so a gate
  # that did not carry it would ask for the wrong buttons.
  run padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
  grep -q -- "--platform n64" "$SEAT_LOG"
  grep -q -- "--title Zelda" "$SEAT_LOG"
}

@test "no controller check installed still launches the game" {
  # It ships with the picker, which is a separate package: the client depends
  # on it the way it depends on padmap, which is to say not at all.
  rm -f "$FAKE_BIN/gotg-seat"
  run padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
  [ ! -f "$SEAT_LOG" ]
}

@test "a controller check that fails does not stop the game" {
  FAKE_SEAT_EXIT=3 run padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
}

@test "the check can be turned off entirely" {
  GOTG_SEAT_GATE=0 run padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
  [ ! -f "$SEAT_LOG" ]
}

@test "play asks about controllers before it runs the emulator" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "emulator ran"\n'
  } >"$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  chmod +x "$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"

  gotg play usa.zelda
  [ "$status" -eq 0 ]
  grep -q -- "--platform n64" "$SEAT_LOG"
}
