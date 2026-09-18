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

@test "a session that owns its compositor is launched outside the sandbox" {
  # padmap's sandbox is a user namespace, where every root-owned file reads as
  # `nobody`. wlroots then refuses /tmp/.X11-unix, Xwayland never starts, and
  # a split-screen game opens to a black screen saying nothing about sandboxes.
  export GOTG_STATE_DIR="$TEST_TMP/state"
  mkdir -p "$GOTG_STATE_DIR/roots/env-split/share/gotg"
  touch "$GOTG_STATE_DIR/roots/env-split/share/gotg/owns-session"
  run bash -c 'source "$GOTG_LIB/common.sh"; source "$GOTG_LIB/env.sh"
    source "$GOTG_LIB/padmap.sh"; PLAY_ATTR=env-split padmap_exec \
    sh -c "echo isolate=\''${PADMAP_NO_ISOLATE:-unset}"'
  [ "$status" -eq 0 ]
  [[ "$output" == *"isolate=1"* ]]
}

@test "an ordinary game keeps the sandbox" {
  export GOTG_STATE_DIR="$TEST_TMP/state"
  mkdir -p "$GOTG_STATE_DIR/roots/env-plain/share/gotg"
  run bash -c 'source "$GOTG_LIB/common.sh"; source "$GOTG_LIB/env.sh"
    source "$GOTG_LIB/padmap.sh"; PLAY_ATTR=env-plain padmap_exec \
    sh -c "echo isolate=\''${PADMAP_NO_ISOLATE:-unset}"'
  [ "$status" -eq 0 ]
  [[ "$output" == *"isolate=unset"* ]]
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

@test "no controller check installed still launches the game, and says so" {
  # It ships with the picker, which is a separate package: the client depends
  # on it the way it depends on padmap, which is to say not at all. Out loud,
  # though -- a check that is quietly not there looks exactly like a check
  # that ran and was happy.
  rm -f "$FAKE_BIN/gotg-seat"
  unset GOTG_SEAT
  run --separate-stderr padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
  [ ! -f "$SEAT_LOG" ]
  [[ "$stderr" == *"no gotg-seat here"* ]]
}

@test "the check is found beside the picker when it is not on PATH" {
  # Nothing puts the picker's directory on PATH for a command nobody types,
  # so "on PATH" alone would miss it on the machines it ships to.
  local elsewhere="$TEST_TMP/profile/bin"
  mkdir -p "$elsewhere"
  mv "$FAKE_BIN/gotg-seat" "$elsewhere/gotg-seat"
  printf '#!%s\nexit 0\n' "$(command -v bash)" >"$elsewhere/gotg-ui"
  chmod +x "$elsewhere/gotg-ui"
  unset GOTG_SEAT
  PATH="$elsewhere:$PATH" run padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
  grep -q -- "--platform n64" "$SEAT_LOG"
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

# --- padmap's configs, into the environment rather than the home ----------------
#
# The daemon writes Ryujinx, Cemu and Dolphin configuration -- bindings, and
# since motion arrived, where to find its DSU server -- under ~/.config. Every
# emulator gotg runs is isolated, so `padmap-rs emit` is pointed at the
# environment's own files, and told every other destination too, because an
# unnamed one means the home.

stub_emit() {
  export EMIT_ARGS="$TEST_TMP/emit.args" EMIT_STDIN="$TEST_TMP/emit.stdin"
  export EMIT_WRITES="${1:-}"
  {
    printf '#!%s\n' "$(command -v bash)"
    cat <<'SHIM'
[[ "$1" == emit ]] || exit 1
shift
printf '%s\n' "$@" >"$EMIT_ARGS"
cat >"$EMIT_STDIN"
# Say which file was written, the way the real one does.
for ((i = 1; i <= $#; i++)); do
  [[ "${!i}" == "--ryujinx-config" ]] && { j=$((i + 1)); printf '%s\n' "${!j}"; }
done
[[ -z "$EMIT_WRITES" ]] || eval "$EMIT_WRITES"
SHIM
  } >"$FAKE_BIN/padmap-rs"
  chmod +x "$FAKE_BIN/padmap-rs"
}

# What the daemon leaves in its runtime directory after seating two pads.
published_two() {
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-runtime"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  cat >"$GOTG_PADMAP_RUNTIME/env.sh" <<'EOS'
# Written by padmap on every republish.
SDL_GAMECONTROLLERCONFIG='0300c9a7de2800000413000001000000,padmap Player 1,a:b0,b:b1,platform:Linux,
0300c9a7de2800000413000002000000,padmap Player 2,a:b0,b:b1,platform:Linux,'
export SDL_GAMECONTROLLERCONFIG
EOS
}

ryujinx_env() {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  jq -n '{emulator: "ryujinx"}' >"$GOTG_ROOTS_DIR/env-switch/share/gotg/pads.json"
}

@test "the pads padmap published are read off its env.sh, by player" {
  published_two
  run padmap_published
  [ "$status" -eq 0 ]
  [ "$(jq 'length' <<<"$output")" = 2 ]
  [ "$(jq -r '.[1].player' <<<"$output")" = 2 ]
  [ "$(jq -r '.[0].guid' <<<"$output")" = 0300c9a7de2800000413000001000000 ]
  [ "$(jq -r '.[0].name' <<<"$output")" = "padmap Player 1" ]
  [[ "$(jq -r '.[1].sdl_line' <<<"$output")" == "0300c9a7de2800000413000002000000,padmap Player 2,"* ]]
}

@test "no daemon output means nothing to emit" {
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/nowhere"
  stub_emit
  ryujinx_env
  run padmap_emit env-switch
  [ "$status" -ne 0 ]
  [ ! -e "$EMIT_ARGS" ]
}

@test "padmap writes into the environment's own Ryujinx config, and nothing into the home" {
  published_two
  stub_emit
  ryujinx_env
  run padmap_emit env-switch
  [ "$status" -eq 0 ]
  local state="$GOTG_STATE_DIR/env/env-switch"
  grep -qxF -- "--ryujinx-config" "$EMIT_ARGS"
  grep -qxF -- "$state/config/Ryujinx/Config.json" "$EMIT_ARGS"
  # Every other destination is named too, and none of them is under the home.
  for flag in --cemu-dir --dolphin-dir --ares-settings --env-file; do
    grep -qxF -- "$flag" "$EMIT_ARGS"
  done
  ! grep -q "$HOME/.config" "$EMIT_ARGS"
  [ "$(jq 'length' "$EMIT_STDIN")" = 2 ]
  [ "$(jq -r '.[0].name' "$EMIT_STDIN")" = "padmap Player 1" ]
}

@test "a Dolphin environment gets its own config directory" {
  published_two
  stub_emit
  mkdir -p "$GOTG_ROOTS_DIR/env-gamecube/share/gotg"
  jq -n '{emulator: "dolphin"}' >"$GOTG_ROOTS_DIR/env-gamecube/share/gotg/pads.json"
  EMIT_WRITES='printf "%s\n" "$GOTG_STATE_DIR/env/env-gamecube/config/dolphin-emu/DSUClient.ini"' \
    run padmap_emit env-gamecube
  [ "$status" -eq 0 ]
  grep -A1 -xF -- "--dolphin-dir" "$EMIT_ARGS" | grep -qxF "$GOTG_STATE_DIR/env/env-gamecube/config/dolphin-emu"
}

@test "an isolated Cemu environment gets its own profiles directory" {
  published_two
  stub_emit
  mkdir -p "$GOTG_ROOTS_DIR/env-wiiu/share/gotg"
  jq -n '{emulator: "cemu", isolate: true}' >"$GOTG_ROOTS_DIR/env-wiiu/share/gotg/pads.json"
  EMIT_WRITES='printf "%s\n" "$GOTG_STATE_DIR/env/env-wiiu/config/Cemu/controllerProfiles/controller0.xml"' \
    run padmap_emit env-wiiu
  [ "$status" -eq 0 ]
  grep -A1 -xF -- "--cemu-dir" "$EMIT_ARGS" | grep -qxF "$GOTG_STATE_DIR/env/env-wiiu/config/Cemu/controllerProfiles"
}

@test "an emulator padmap does not write for is left to gotg" {
  published_two
  stub_emit
  mkdir -p "$GOTG_ROOTS_DIR/env-n64/share/gotg"
  jq -n '{emulator: "ares"}' >"$GOTG_ROOTS_DIR/env-n64/share/gotg/pads.json"
  run padmap_emit env-n64
  [ "$status" -ne 0 ]
  [ ! -e "$EMIT_ARGS" ]
}
