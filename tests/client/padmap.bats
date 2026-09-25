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
    printf 'printf "env PADMAP_NO_AUTOSETUP=%%s PADMAP_NO_AUTOATTACH=%%s PADMAP_HOLD_SECONDS=%%s\\n" "${PADMAP_NO_AUTOSETUP-unset}" "${PADMAP_NO_AUTOATTACH-unset}" "${PADMAP_HOLD_SECONDS-unset}" >>"$PADMAP_LOG"\n'
    printf 'exit "${FAKE_PADMAP_EXIT:-0}"\n'
  } >"$FAKE_BIN/padmap"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "padmap-rs %%s\\n" "$*" >>"$PADMAP_LOG"\n'
    printf 'while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done; shift\n'
    printf 'exec "$@"\n'
  } >"$FAKE_BIN/padmap-rs"
  export SEAT_LOG="$TEST_TMP/seat.log"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "gotg-seat %%s\\n" "$*" >>"$SEAT_LOG"\n'
    # What Steam's environment looked like by the time the gate ran: the
    # gate is SDL, and Steam's ignore list would blind it exactly as it
    # blinds a game.
    printf 'printf "env SDL_GAMECONTROLLER_IGNORE_DEVICES=%%s LD_PRELOAD=%%s\\n" "${SDL_GAMECONTROLLER_IGNORE_DEVICES-unset}" "${LD_PRELOAD-unset}" >>"$SEAT_LOG"\n'
    printf '[ -z "${ORDER_LOG:-}" ] || echo seat >>"$ORDER_LOG"\n'
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

@test "the daemon is asked for before a launch, unseated and following this shell" {
  # Nobody is seated when a game opens, and the daemon goes when the game
  # does. The pid is this shell's, which the emulator inherits by exec.
  run padmap_ensure
  [ "$status" -eq 0 ]
  grep -q "padmap ensure-daemon --fresh --follow [0-9]" "$PADMAP_LOG"
  # And told the two rules before it started: no session of its own, and no
  # seat but by a hold.
  grep -q "env PADMAP_NO_AUTOSETUP=1 PADMAP_NO_AUTOATTACH=1 PADMAP_HOLD_SECONDS=1.5" "$PADMAP_LOG"
}

@test "a gate the picker met still waits for padmap to publish" {
  # Only the asking is skipped. The daemon is still ensured and the publish
  # still waited for, because the bindings written after this read padmap's
  # pad list -- and Four Swords Adventures came up with player two on the
  # keyboard when that list was read before padmap had finished.
  export GOTG_SEAT="$FAKE_BIN/gotg-seat"
  GOTG_SEAT_MET=1 padmap_seat_gate n64 "Donkey Kong 64"
  grep -q "padmap ensure-daemon" "$PADMAP_LOG"
}

@test "the gate is not asked for twice when the picker already met it" {
  # The picker runs the gate in its own window before it execs here, so the
  # seats are already taken and the screen has already been seen. Saying so
  # is not the same as turning the check off.
  export GOTG_SEAT="$FAKE_BIN/gotg-seat"
  GOTG_SEAT_MET=1 padmap_seat_gate n64 "Donkey Kong 64"
  [ ! -s "$SEAT_LOG" ] || fail "the gate ran again: $(cat "$SEAT_LOG")"
}

@test "and it is asked for when nobody has met it" {
  export GOTG_SEAT="$FAKE_BIN/gotg-seat"
  padmap_seat_gate n64 "Donkey Kong 64"
  grep -q "n64" "$SEAT_LOG"
}

@test "a seat takes a hold long enough to be deliberate, not a quarter second" {
  # padmap's own default claims a seat in 0.25s, which is short enough that
  # picking a controller up takes one. The picker and the gate ask for the
  # length on every `seating`; a session -- padmap's wizard -- takes what the
  # daemon was started with, so it is set here too.
  run padmap_ensure
  [ "$status" -eq 0 ]
  grep -q "PADMAP_HOLD_SECONDS=1.5" "$PADMAP_LOG"
}

@test "a hold length somebody set by hand is left alone" {
  PADMAP_HOLD_SECONDS=0.4 padmap_ensure
  grep -q "PADMAP_HOLD_SECONDS=0.4" "$PADMAP_LOG"
}

@test "it is asked for once, not once per launch" {
  padmap_ensure
  padmap_ensure
  [ "$(grep -c "ensure-daemon" "$PADMAP_LOG")" = 1 ]
}

@test "a daemon that has just started is given a moment to publish" {
  # ensure-daemon returns before the first publish. A launch that started
  # the daemon read the mappings file a moment before it existed and handed
  # the game no controllers at all.
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  # A daemon that publishes half a second after it is asked to start.
  cat >"$FAKE_BIN/padmap" <<EOF
#!$(command -v bash)
echo "\$*" >>"$PADMAP_LOG"
if [ "\$1" = ensure-daemon ]; then
  echo "no daemon running; starting one"; echo "daemon up, build test"
  (sleep 0.5; echo 'export X=1' >"$GOTG_PADMAP_RUNTIME/env.sh") &
fi
exit 0
EOF
  chmod +x "$FAKE_BIN/padmap"
  run --separate-stderr padmap_ensure
  [ "$status" -eq 0 ]
  [ -s "$GOTG_PADMAP_RUNTIME/env.sh" ]
  [[ "$stderr" != *"published no controllers"* ]]
}

@test "a daemon that never publishes is waited on only so long" {
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt-never"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  printf '#!%s\necho "daemon up, build test"\n' "$BASH" >"$FAKE_BIN/padmap"
  chmod +x "$FAKE_BIN/padmap"
  GOTG_PADMAP_PUBLISH_WAIT=3 run --separate-stderr padmap_ensure
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"published no controllers"* ]]
}

@test "a leftover mappings file from an earlier daemon does not count as published" {
  # The file survives its daemon within a login session, so an existence
  # test passed on a leftover while the new daemon had said nothing yet.
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt-stale"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  echo 'export STALE=1' >"$GOTG_PADMAP_RUNTIME/env.sh"
  sleep 0.05
  printf '#!%s\necho "daemon up, build test"\n' "$BASH" >"$FAKE_BIN/padmap"
  chmod +x "$FAKE_BIN/padmap"
  GOTG_PADMAP_PUBLISH_WAIT=3 run --separate-stderr padmap_ensure
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"published no controllers"* ]]
}

@test "a daemon that was already current is not waited on" {
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt-current"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  printf '#!%s\necho "daemon is current (build test)"\n' "$BASH" >"$FAKE_BIN/padmap"
  chmod +x "$FAKE_BIN/padmap"
  # No mappings file at all -- nobody seated yet -- and no waiting for one:
  # that would be waiting on a button press.
  local before after
  before=$(date +%s)
  GOTG_PADMAP_PUBLISH_WAIT=30 run --separate-stderr padmap_ensure
  after=$(date +%s)
  [ "$status" -eq 0 ]
  [ $((after - before)) -lt 2 ]
  [[ "$stderr" != *"published no controllers"* ]]
}

@test "a check that failed is asked again next time" {
  # The latch used to be set on the way out regardless, so one bad start at
  # the picker disabled the check for every game launched from it.
  FAKE_PADMAP_EXIT=1 run padmap_ensure
  [ "$status" -eq 0 ]
  FAKE_PADMAP_EXIT=1 padmap_ensure
  [ -z "${PADMAP_SKIP_DAEMON_CHECK:-}" ]
  padmap_ensure
  [ "${PADMAP_SKIP_DAEMON_CHECK:-}" = 1 ]
}

# --- the keeper ---

# A stand-in daemon whose --check answer is scripted: one line per call.
keeper_daemon() {
  export CHECK_SCRIPT="$TEST_TMP/check-script"
  export CHECK_COUNT="$TEST_TMP/check-count"
  printf '%s\n' "$@" >"$CHECK_SCRIPT"
  : >"$CHECK_COUNT"
  cat >"$FAKE_BIN/padmap" <<EOF
#!$(command -v bash)
echo "\$*" >>"$PADMAP_LOG"
if [ "\$1" = ensure-daemon ] && [ "\$2" = --check ]; then
  n=\$(wc -l <"$CHECK_COUNT"); echo x >>"$CHECK_COUNT"
  line=\$(sed -n "\$((n + 1))p" "$CHECK_SCRIPT")
  [ -n "\$line" ] || line="daemon is current"
  echo "\$line"
  case "\$line" in *current*) exit 0 ;; *) exit 1 ;; esac
fi
exit 0
EOF
  chmod +x "$FAKE_BIN/padmap"
}

@test "the keeper starts a daemon that has gone, once" {
  keeper_daemon "no daemon running" "daemon is current"
  sleep 1.2 &
  local game=$!
  GOTG_PADMAP_KEEPER_INTERVAL=0.2 padmap_keeper_start "$game"
  wait "$game"
  sleep 0.6
  # Following the game's pid, and not fresh: the seats it had are the ones
  # the level is being played with.
  [ "$(grep -c "^ensure-daemon --follow $game\$" "$PADMAP_LOG")" -eq 1 ]
}

@test "the keeper leaves a daemon running older code alone" {
  # That is a sync's business; swapping the daemon mid-level would drop the
  # clones the game is using.
  keeper_daemon "daemon is running older code:" "daemon is running older code:"
  sleep 1.0 &
  local game=$!
  GOTG_PADMAP_KEEPER_INTERVAL=0.2 padmap_keeper_start "$game"
  wait "$game"
  sleep 0.6
  [ "$(grep -c '^ensure-daemon --follow' "$PADMAP_LOG")" -eq 0 ]
  [ "$(grep -c 'ensure-daemon --check' "$PADMAP_LOG")" -ge 1 ]
}

@test "the keeper stops when the game does" {
  keeper_daemon "daemon is current"
  sleep 0.5 &
  local game=$!
  GOTG_PADMAP_KEEPER_INTERVAL=0.2 padmap_keeper_start "$game"
  local keeper=$!
  wait "$game"
  sleep 1.0
  ! kill -0 "$keeper" 2>/dev/null
}

@test "the keeper can be switched off" {
  keeper_daemon "no daemon running"
  sleep 0.6 &
  local game=$!
  GOTG_PADMAP_KEEPER=0 GOTG_PADMAP_KEEPER_INTERVAL=0.2 padmap_keeper_start "$game"
  wait "$game"
  sleep 0.5
  [ ! -s "$PADMAP_LOG" ]
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

@test "the check is found in a Nix profile when PATH has none" {
  # Which is every launch from Steam: its environment carries no profile
  # directory, so `command -v gotg-seat` finds nothing and the check was
  # skipped -- with the warning going to a log nobody reads.
  local profile="$TEST_TMP/home/.nix-profile/bin"
  mkdir -p "$profile"
  printf '#!/bin/sh\n' >"$profile/gotg-seat"
  chmod +x "$profile/gotg-seat"
  run env -u GOTG_SEAT HOME="$TEST_TMP/home" PATH=/usr/bin:/bin "$BASH" -c \
    "source '$GOTG_LIB/common.sh'; source '$GOTG_LIB/padmap.sh'; padmap_seat_bin"
  [ "$status" -eq 0 ]
  [[ "$output" == "$profile/gotg-seat" ]]
}

@test "a machine with no check anywhere still says so" {
  run env -u GOTG_SEAT HOME="$TEST_TMP/empty" PATH=/usr/bin:/bin "$BASH" -c \
    "source '$GOTG_LIB/common.sh'; source '$GOTG_LIB/padmap.sh'; padmap_seat_bin"
  [ "$status" -ne 0 ]
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

@test "a Steam launch meets the controller check first, with Steam's blindfold off" {
  # Steam is its own environment: a sparse PATH, SDL told to ignore the very
  # pads padmap publishes, and its overlay preloaded into everything. The
  # launcher it runs is the one `gotg steam add` writes, and this runs that
  # launcher under those conditions and reads what reached the gate.
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  export ORDER_LOG="$TEST_TMP/order"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo game >>"$ORDER_LOG"\n'
    printf 'echo "emulator ran"\n'
  } >"$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  chmod +x "$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"

  export GOTG_STEAM_SHORTCUTS="$TEST_TMP/shortcuts.vdf"
  gotg steam add usa.zelda
  [ "$status" -eq 0 ]
  local launcher="$GOTG_GAMES_DIR/n64/play-usa.zelda.sh"
  [ -x "$launcher" ]

  # Where the launcher looks for gotg: a stable path under the state dir,
  # never PATH -- and under Steam, never the store.
  local xdg="$TEST_TMP/steam-xdg"
  mkdir -p "$xdg/gotg/app/bin"
  ln -s "$GOTG_BIN" "$xdg/gotg/app/bin/gotg"

  run env XDG_STATE_HOME="$xdg" \
      SDL_GAMECONTROLLER_IGNORE_DEVICES="0x28de/0x1142,0x045e/0x028e" \
      SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="0x0000/0x0000" \
      LD_PRELOAD="/opt/steam/ubuntu12_64/gameoverlayrenderer.so" \
      bash "$launcher"
  [ "$status" -eq 0 ]
  # Steam swallows stdout; the launcher keeps its own log.
  grep -q "emulator ran" "$xdg/gotg/logs/usa.zelda.log"
  # The gate first, the game second.
  [ "$(paste -sd, "$ORDER_LOG")" = "seat,game" ]
  grep -q -- "--platform n64" "$SEAT_LOG"
  grep -q "env SDL_GAMECONTROLLER_IGNORE_DEVICES=unset LD_PRELOAD=unset" "$SEAT_LOG"
}

@test "the bindings are written again after the gate, from what it seated" {
  # The ones play_prepare wrote were from before anyone held a button. On a
  # real launch the Steam Controller was seated, published, and dead in the
  # game, because ares' port 1 named the raw Xbox pad the sandbox then hid.
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  export ORDER_LOG="$TEST_TMP/order"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo game >>"$ORDER_LOG"\n'
  } >"$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  chmod +x "$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  # An environment with pads to bind, and an enumerator that says so -- and
  # records how it was asked, since how it is asked decides what it can see.
  echo '{"emulator":"ares","console":"Nintendo64"}' >"$GOTG_ROOTS_DIR/env-n64/share/gotg/pads.json"
  export GOTG_PADS="$FAKE_BIN/gotg-pads"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo pads >>"$ORDER_LOG"\n'
    printf 'echo "pads hidapi=${SDL_JOYSTICK_HIDAPI-unset} steam=${SDL_JOYSTICK_HIDAPI_STEAM-unset} config=${SDL_GAMECONTROLLERCONFIG:+set}" >>"$SEAT_LOG"\n'
    printf 'echo "[]"\n'
  } >"$GOTG_PADS"
  chmod +x "$GOTG_PADS"
  # The gate seats somebody: padmap publishes, as a claim would make it.
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo seat >>"$ORDER_LOG"\n'
    printf "cat '%s' >\"\$GOTG_PADMAP_RUNTIME/env.sh\"\n" "$BATS_TEST_DIRNAME/fixtures/padmap-env-one-player.sh"
  } >"$FAKE_BIN/gotg-seat"
  chmod +x "$FAKE_BIN/gotg-seat"
  mkdir -p "$TEST_TMP/data"
  cp "$(dirname "$GOTG_BIN")/../share/gotg/data/ares-pads.json" "$TEST_TMP/data/"
  export GOTG_DATA="$TEST_TMP/data"

  gotg play usa.zelda
  [ "$status" -eq 0 ]
  # Before the gate, the gate, again after it, then the game.
  [ "$(paste -sd, "$ORDER_LOG")" = "pads,seat,pads,game" ]
  # Before the gate nothing was published, so the enumerator ran as it
  # always has. After it, padmap's mapping in hand and hidapi off, or the
  # clone of a Steam Controller is not in the list at all.
  [ "$(grep -c '^pads ' "$SEAT_LOG")" -eq 2 ]
  [ "$(sed -n '1{/^pads /p}' <(grep '^pads ' "$SEAT_LOG"))" = "pads hidapi=unset steam=unset config=" ]
  [ "$(grep '^pads ' "$SEAT_LOG" | sed -n 2p)" = "pads hidapi=0 steam=0 config=set" ]
}

@test "the publish is waited for after the gate, not before it" {
  # Waiting before the gate waited three seconds for a publish nobody could
  # have caused yet, and warned that nothing was published over a launch
  # about to seat somebody.
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  export ORDER_LOG="$TEST_TMP/order"
  cat >"$FAKE_BIN/padmap" <<EOF
#!$(command -v bash)
echo "\$*" >>"$PADMAP_LOG"
if [ "\$1" = ensure-daemon ]; then
  echo "no daemon running; starting one"; echo "daemon up, build test"
fi
exit 0
EOF
  # The gate is what publishes: this one writes env.sh, as a claim would.
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo seat >>"$ORDER_LOG"\n'
    printf 'echo "export SDL_GAMECONTROLLERCONFIG=x" >"$GOTG_PADMAP_RUNTIME/env.sh"\n'
  } >"$FAKE_BIN/gotg-seat"
  chmod +x "$FAKE_BIN/padmap" "$FAKE_BIN/gotg-seat"
  GOTG_PADMAP_PUBLISH_WAIT=20 run --separate-stderr padmap_seat_gate n64 "Zelda"
  [ "$status" -eq 0 ]
  [ "$(paste -sd, "$ORDER_LOG")" = "seat" ]
  [[ "$stderr" != *"published no controllers"* ]]
}

@test "controllers list --as-game enumerates the way a game is launched" {
  # Inside the sandbox, hidapi off, Steam's ignore list gone: the view a
  # port gets, which is not the view `list` gets from the desktop.
  export GOTG_PADS="$FAKE_BIN/gotg-pads"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "pads hidapi=${SDL_JOYSTICK_HIDAPI-unset} ignore=${SDL_GAMECONTROLLER_IGNORE_DEVICES-unset}" >>"$SEAT_LOG"\n'
    printf 'echo "[]"\n'
  } >"$GOTG_PADS"
  chmod +x "$GOTG_PADS"
  SDL_GAMECONTROLLER_IGNORE_DEVICES="0x28de/0x1142" gotg controllers list --as-game
  [ "$status" -eq 0 ]
  grep -q "padmap-rs exec -- " "$PADMAP_LOG"
  grep -q "pads hidapi=0 ignore=unset" "$SEAT_LOG"
  [[ "$stderr" == *"no controllers visible"* ]]
}

# --- which pad identity a game's clones get ---------------------------------

pads_manifest() {
  local attr="$1"
  mkdir -p "$GOTG_ROOTS_DIR/$attr/share/gotg"
  cat >"$GOTG_ROOTS_DIR/$attr/share/gotg/pads.json"
}

@test "an environment that asks for nothing gets padmap's default" {
  # Mirror, which is what nearly everything wants: an emulator told to bind
  # a controller expects to find that controller.
  fake_env env-plain
  pads_manifest env-plain <<<'{"emulator":"ares","console":"Nintendo64"}'
  run padmap_identity env-plain
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a decompiled port's environment asks for the Xbox 360 identity" {
  fake_env env-dk64
  pads_manifest env-dk64 <<<'{"emulator":"ares","console":"Nintendo64","identity":"xbox360"}'
  run padmap_identity env-dk64
  [ "$status" -eq 0 ]
  [ "$output" = "xbox360" ]
}

@test "Ryujinx never gets it, whatever the environment says" {
  # Every clone is 045e:028e under it and Ryujinx blanks the name CRC to
  # make its device id, so four players would land on one id and one seat.
  fake_env env-switch
  pads_manifest env-switch <<<'{"emulator":"ryujinx","identity":"xbox360"}'
  run --separate-stderr padmap_identity env-switch
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"Ryujinx cannot tell two clones apart"* ]]
}

@test "applying it exports the identity and asks past the daemon latch" {
  # The picker started its daemon mirrored and latched the check on its way
  # here; without clearing it nothing would ask, and the game would get the
  # picker's clones.
  fake_env env-dk64
  pads_manifest env-dk64 <<<'{"emulator":"ares","identity":"xbox360"}'
  export PADMAP_SKIP_DAEMON_CHECK=1
  padmap_identity_apply env-dk64
  [ "$PADMAP_PAD_IDENTITY" = "xbox360" ]
  [ -z "${PADMAP_SKIP_DAEMON_CHECK:-}" ]
}

@test "applying nothing leaves the latch and the environment alone" {
  fake_env env-plain
  pads_manifest env-plain <<<'{"emulator":"ares"}'
  export PADMAP_SKIP_DAEMON_CHECK=1
  padmap_identity_apply env-plain
  [ -z "${PADMAP_PAD_IDENTITY:-}" ]
  [ "$PADMAP_SKIP_DAEMON_CHECK" = 1 ]
}

@test "somebody who set the identity themselves keeps it" {
  fake_env env-dk64
  pads_manifest env-dk64 <<<'{"emulator":"ares","identity":"xbox360"}'
  export PADMAP_PAD_IDENTITY=mirror PADMAP_SKIP_DAEMON_CHECK=1
  padmap_identity_apply env-dk64
  [ "$PADMAP_PAD_IDENTITY" = mirror ]
  [ "$PADMAP_SKIP_DAEMON_CHECK" = 1 ]
}

@test "a launch hands the identity to the daemon before it is asked after" {
  add_game n64 "usa.dk64.z64" "rom" "DK64"
  gotg refresh
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  pads_manifest env-n64 <<<'{"emulator":"ares","console":"Nintendo64","identity":"xbox360"}'
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "emulator ran"\n'
  } >"$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  chmod +x "$GOTG_ROOTS_DIR/env-n64/bin/gotg-play"
  mkdir -p "$TEST_TMP/data"
  cp "$(dirname "$GOTG_BIN")/../share/gotg/data/ares-pads.json" "$TEST_TMP/data/"
  export GOTG_DATA="$TEST_TMP/data"
  # The launch that a pick from the grid is: the picker's latch already set.
  export PADMAP_SKIP_DAEMON_CHECK=1
  cat >"$FAKE_BIN/padmap" <<EOF
#!$(command -v bash)
echo "\$* identity=\${PADMAP_PAD_IDENTITY-unset}" >>"$PADMAP_LOG"
exit 0
EOF
  chmod +x "$FAKE_BIN/padmap"

  gotg play usa.dk64
  [ "$status" -eq 0 ]
  grep -q "ensure-daemon --fresh --follow [0-9]* identity=xbox360" "$PADMAP_LOG"
}

@test "a published clone turns SDL's Steam driver off for the rest of the launch" {
  # SDL3's triton driver claims 28de:* whether the device behind them is
  # hidraw or not, and padmap's clone of a Steam Controller wears them.
  # Measured with a uinput pad at 28de:1304: listed with the hint off, absent
  # with it on. Four Swords Adventures bound its first GBA to the keyboard
  # for this while padmap was saying it had seated the pad.
  export SDL_JOYSTICK_HIDAPI_STEAM=1
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt-steam"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  echo "export SDL_GAMECONTROLLERCONFIG=03000000de2800000413,padmap Player 1,a:b0," \
    >"$GOTG_PADMAP_RUNTIME/env.sh"
  GOTG_SEAT_MET=1 padmap_seat_gate gamecube "Four Swords Adventures"
  [ "$SDL_JOYSTICK_HIDAPI_STEAM" = 0 ]
  [ "$SDL_JOYSTICK_HIDAPI" = 0 ]
}

@test "and a launch that met no padmap keeps it, because a raw puck needs it" {
  # The hint is the only reason a Steam Controller works at all without
  # padmap: it has no evdev node to fall back to.
  export SDL_JOYSTICK_HIDAPI_STEAM=1
  export GOTG_PADMAP_RUNTIME="$TEST_TMP/padmap-rt-nothing"
  mkdir -p "$GOTG_PADMAP_RUNTIME"
  GOTG_SEAT_MET=1 padmap_seat_gate gamecube "Four Swords Adventures"
  [ "$SDL_JOYSTICK_HIDAPI_STEAM" = 1 ]
}

# --- seats held open for a game that binds its players once ------------------

@test "an environment that binds its players at launch reserves their seats" {
  # Four Swords binds a GBA per player when it starts; a pad paired mid-game
  # had no binding and Start did nothing.
  fake_env env-fsa
  pads_manifest env-fsa <<<'{"emulator":"dolphin","reserve":4}'
  run padmap_reserve env-fsa
  [ "$status" -eq 0 ]
  [ "$output" = "--reserve 4" ]
}

@test "an environment that asks for no seats reserves none" {
  fake_env env-plain
  pads_manifest env-plain <<<'{"emulator":"dolphin"}'
  run padmap_reserve env-plain
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a reservation that is not a number of seats is ignored" {
  fake_env env-odd
  pads_manifest env-odd <<<'{"emulator":"dolphin","reserve":"all; rm -rf /"}'
  run padmap_reserve env-odd
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "Ryujinx never reserves: it needs the identity Ryujinx cannot use" {
  fake_env env-switch
  pads_manifest env-switch <<<'{"emulator":"ryujinx","reserve":4}'
  run --separate-stderr padmap_reserve env-switch
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"not reserving seats"* ]]
}

@test "the reservation rides on the exec that launches the game" {
  fake_env env-fsa
  pads_manifest env-fsa <<<'{"emulator":"dolphin","reserve":2}'
  run bash -c 'source "$GOTG_LIB/common.sh"; source "$GOTG_LIB/env.sh"
    source "$GOTG_LIB/padmap.sh"; PLAY_ATTR=env-fsa padmap_exec echo played'
  [ "$status" -eq 0 ]
  [[ "$output" == *"played"* ]]
  grep -q "padmap-rs exec --reserve 2 -- echo played" "$PADMAP_LOG"
}

@test "an ordinary game is exec'd with no reservation" {
  fake_env env-plain
  pads_manifest env-plain <<<'{"emulator":"dolphin"}'
  run bash -c 'source "$GOTG_LIB/common.sh"; source "$GOTG_LIB/env.sh"
    source "$GOTG_LIB/padmap.sh"; PLAY_ATTR=env-plain padmap_exec echo played'
  [ "$status" -eq 0 ]
  grep -q "padmap-rs exec -- echo played" "$PADMAP_LOG"
}
