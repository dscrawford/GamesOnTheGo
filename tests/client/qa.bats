#!/usr/bin/env bats
# The QA analyzers, against synthesized media whose defects are chosen: a sine
# wave, silence, a two-second gap; motion, black, a frozen frame. What ffmpeg
# reports about real captures is asserted here against inputs where the right
# answer is known by construction.

bats_require_minimum_version 1.5.0

load helper

setup_file() {
  export QA_FIX="$BATS_FILE_TMPDIR/fix"
  mkdir -p "$QA_FIX"
  local ff="ffmpeg -hide_banner -loglevel error -y"
  $ff -f lavfi -i sine=frequency=440:duration=6 -ar 48000 "$QA_FIX/sine.wav"
  $ff -f lavfi -i anullsrc=r=48000:cl=stereo:d=6 "$QA_FIX/silent.wav"
  $ff -f lavfi -i sine=frequency=440:duration=6 -ar 48000 \
    -af "volume=enable='between(t,2,4)':volume=0" "$QA_FIX/gap.wav"
  $ff -f lavfi -i testsrc2=duration=6:size=320x240:rate=30 "$QA_FIX/moving.mkv"
  $ff -f lavfi -i color=black:duration=6:size=320x240:rate=30 "$QA_FIX/black.mkv"
  $ff -f lavfi -i color=red:duration=6:size=320x240:rate=30 "$QA_FIX/still.mkv"
}

setup() {
  setup_env
  load_client_libs
}

# --- audio ---

@test "audio: a sine wave has signal and no silence" {
  run -0 qa_audio_stats "$QA_FIX/sine.wav"
  [ "$(jq '.rms_db > -45' <<<"$output")" = "true" ]
  [ "$(jq '.longest_silence < 0.5' <<<"$output")" = "true" ]
}

@test "audio: silence is reported as silence, not as an error" {
  run -0 qa_audio_stats "$QA_FIX/silent.wav"
  [ "$(jq '.rms_db <= -90' <<<"$output")" = "true" ]
  [ "$(jq '.longest_silence > 5' <<<"$output")" = "true" ]
}

@test "audio: a two-second dropout is found and measured" {
  run -0 qa_audio_stats "$QA_FIX/gap.wav"
  [ "$(jq '.longest_silence > 1.5 and .longest_silence < 2.5' <<<"$output")" = "true" ]
}

# --- video ---

@test "video: motion is neither black nor frozen" {
  run -0 qa_video_stats "$QA_FIX/moving.mkv" 1
  [ "$(jq '.black_total < 0.5' <<<"$output")" = "true" ]
  [ "$(jq '.freeze_in_window < 0.5' <<<"$output")" = "true" ]
}

@test "video: a black clip is black, and also frozen" {
  run -0 qa_video_stats "$QA_FIX/black.mkv" 1
  [ "$(jq '.black_total > 4' <<<"$output")" = "true" ]
}

@test "video: a still frame freezes without being black" {
  run -0 qa_video_stats "$QA_FIX/still.mkv" 1
  [ "$(jq '.black_total < 0.5' <<<"$output")" = "true" ]
  [ "$(jq '.freeze_in_window > 2' <<<"$output")" = "true" ]
}

# --- golden frames ---

@test "golden: a frame matches itself" {
  qa_frame "$QA_FIX/still.mkv" 2 "$TEST_TMP/a.png"
  run -0 qa_phash "$TEST_TMP/a.png" "$TEST_TMP/a.png"
  [ "$(jq -n "$output < 1" 2>/dev/null)" = "true" ]
}

@test "golden: different scenes are far apart" {
  qa_frame "$QA_FIX/still.mkv" 2 "$TEST_TMP/a.png"
  qa_frame "$QA_FIX/moving.mkv" 2 "$TEST_TMP/b.png"
  distance="$(qa_phash "$TEST_TMP/a.png" "$TEST_TMP/b.png")"
  [ "$(jq -n "$distance > 5")" = "true" ]
}

# --- verdict ---

# A run directory as a session leaves it: captures plus the emulator's exit.
make_rundir() {
  local dir="$1" wav="$2" video="$3" status="${4:-0}"
  mkdir -p "$dir"
  cp "$wav" "$dir/audio.wav"
  cp "$video" "$dir/video.mkv"
  printf '%s\n' "$status" >"$dir/status"
}

@test "verdict: good captures pass every axis" {
  make_rundir "$TEST_TMP/run" "$QA_FIX/sine.wav" "$QA_FIX/moving.mkv"
  run -0 qa_verdict "$TEST_TMP/run" 1 6
  [ "$(jq .pass "$TEST_TMP/run/verdict.json")" = "true" ]
  [ "$(jq .checks.graphics.pass "$TEST_TMP/run/verdict.json")" = "null" ]
}

@test "verdict: silence fails audio and only audio" {
  make_rundir "$TEST_TMP/run" "$QA_FIX/silent.wav" "$QA_FIX/moving.mkv"
  run -1 qa_verdict "$TEST_TMP/run" 1 6
  [ "$(jq .pass "$TEST_TMP/run/verdict.json")" = "false" ]
  [ "$(jq .checks.audio.pass "$TEST_TMP/run/verdict.json")" = "false" ]
  [ "$(jq .checks.controller.pass "$TEST_TMP/run/verdict.json")" = "true" ]
}

@test "verdict: a frozen screen fails the controller axis" {
  make_rundir "$TEST_TMP/run" "$QA_FIX/sine.wav" "$QA_FIX/still.mkv"
  run -1 qa_verdict "$TEST_TMP/run" 1 6
  [ "$(jq .checks.controller.pass "$TEST_TMP/run/verdict.json")" = "false" ]
}

@test "verdict: an emulator that died early fails boots" {
  make_rundir "$TEST_TMP/run" "$QA_FIX/sine.wav" "$QA_FIX/moving.mkv" 127
  run -1 qa_verdict "$TEST_TMP/run" 1 6
  [ "$(jq .checks.boots.pass "$TEST_TMP/run/verdict.json")" = "false" ]
}

@test "verdict: a matching golden passes graphics" {
  make_rundir "$TEST_TMP/run" "$QA_FIX/sine.wav" "$QA_FIX/moving.mkv"
  qa_frame "$QA_FIX/moving.mkv" 4 "$TEST_TMP/run/golden.png"
  run -0 qa_verdict "$TEST_TMP/run" 1 6
  [ "$(jq .checks.graphics.pass "$TEST_TMP/run/verdict.json")" = "true" ]
}

@test "verdict: a wrong golden fails graphics" {
  make_rundir "$TEST_TMP/run" "$QA_FIX/sine.wav" "$QA_FIX/moving.mkv"
  qa_frame "$QA_FIX/still.mkv" 4 "$TEST_TMP/run/golden.png"
  run -1 qa_verdict "$TEST_TMP/run" 1 6
  [ "$(jq .checks.graphics.pass "$TEST_TMP/run/verdict.json")" = "false" ]
}

@test "verdict: a capture much shorter than the run fails video and controller" {
  # A screen that stops changing stops producing frames — a 6s capture of a
  # 45s run is a stuck emulator, however healthy each captured frame looks.
  make_rundir "$TEST_TMP/run" "$QA_FIX/sine.wav" "$QA_FIX/moving.mkv"
  run -1 qa_verdict "$TEST_TMP/run" 1 45
  [ "$(jq .checks.video.pass "$TEST_TMP/run/verdict.json")" = "false" ]
  [ "$(jq .checks.controller.pass "$TEST_TMP/run/verdict.json")" = "false" ]
}

# --- process ancestry, which decides whose audio is the run's ---

# The name in /proc/<pid>/stat is the process's own to choose and may hold
# spaces, so ppid is not a field number. Reading it as one returned the tail of
# the name instead — measured: `npm exec open-w` answered "open-w)" — the walk
# failed, the emulator's audio was never moved onto the QA sink, and the run
# graded silent. A copy of the shell under such a name reproduces it.
@test "ancestry: a child whose name contains a space is still found" {
  # The shell's own binary, not coreutils: coreutils is a multi-call binary
  # that dispatches on argv[0] and refuses to run under any other name. The
  # trailing `:` keeps bash from exec'ing straight into sleep, which would
  # replace the name being tested.
  cp "$(readlink -f "/proc/$BASHPID/exe")" "$TEST_TMP/a b"
  chmod +x "$TEST_TMP/a b"
  "$TEST_TMP/a b" -c 'sleep 20; :' &
  local child=$! parent=$BASHPID
  # Give it long enough to be in /proc under its new name.
  sleep 0.5
  run qa_pid_under "$child" "$parent"
  kill "$child" 2>/dev/null || true
  [ "$status" -eq 0 ]
}

@test "ancestry: a process is under its own parent, and not under a stranger" {
  run qa_pid_under "$$" "$PPID"
  [ "$status" -eq 0 ]
  run qa_pid_under "$$" 99999999
  [ "$status" -ne 0 ]
}

# A name can be chosen to look like a pid, so anything non-numeric ends the
# walk rather than being followed.
@test "ancestry: a non-numeric pid is refused" {
  run qa_pid_under "a b 1" 1
  [ "$status" -ne 0 ]
}

# --- the command ---

@test "qa: no id is an error with a usage line" {
  gotg qa
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg qa"* ]]
}

@test "qa: answers --help" {
  gotg qa --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage: gotg qa"* ]]
}

# --- the catalog ---

@test "a game imported since the cache was written is found after one refresh" {
  start_saves_service
  write_api_config
  add_game n64 "usa.old.z64" "rom" "Old"
  gotg refresh
  add_game n64 "usa.new.z64" "rom" "New"

  # The lookup is the first thing qa does; past it the run wants a pad
  # device and a compositor this test has no business starting, so the
  # messages are what is checked.
  gotg qa usa.new --duration 1
  [[ "$stderr" != *"no game called 'usa.new'"* ]]
  [[ "$stderr" == *"catalog updated"* ]]
  gotg qa usa.nowhere --duration 1
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no game called 'usa.nowhere'"* ]]
  stop_saves_service
}

# --- one directory per run ---

@test "two runs started in the same second get directories of their own" {
  local a b
  a="$(qa_new_rundir)"
  b="$(qa_new_rundir)"
  [ -d "$a" ] && [ -d "$b" ]
  [ "$a" != "$b" ]
  [[ "$a" == "$GOTG_STATE_DIR/qa/runs/"[0-9]*-* ]]
}


@test "a qa run brings its own pad, so the controller gate is turned off" {
  # Nobody is in front of a QA run to hold a button, and the gate waits for
  # one until somebody does. On a machine with a padmap daemon and a pad it
  # has never mapped, that is a run which never starts and never says why.
  grep -A6 'export GOTG_NO_DIALOG=1' "$GOTG_LIB/cmd-qa.sh" | grep -qx '  export GOTG_SEAT_GATE=0'
}

# --- machines: one box pretending to be several ------------------------------
#
# Every launch that worked here and failed on the Deck failed on a condition
# that could have been reproduced here: no host GL, X11 only, a C locale. A
# profile is those conditions, applied to the game alone.

@test "a machine profile is the exports and unsets the session applies" {
  run qa_machine_env deck
  [ "$status" -eq 0 ]
  [[ "$output" == *"unset WAYLAND_DISPLAY"* ]]
  [[ "$output" == *"export GOTG_FOREIGN_GL='1'"* ]]
  [[ "$output" == *"export LANG='C'"* ]]
  # And the session can source what it is given, and end up in that machine.
  local out
  out="$(env WAYLAND_DISPLAY=wayland-9 bash -c "$(qa_machine_env deck); printf '%s|%s|%s' \"\${WAYLAND_DISPLAY:-gone}\" \"\$GOTG_FOREIGN_GL\" \"\$LANG\"")"
  [ "$out" = "gone|1|C" ]
}

@test "the desktop is the machine this is, and changes nothing" {
  run qa_machine_env desktop
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a machine nobody has described is refused, naming the ones that are" {
  run --separate-stderr qa_machine_env steamos-4
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no such machine profile: steamos-4"* ]]
  [[ "$stderr" == *"deck"* ]]
  [[ "$stderr" == *"desktop"* ]]
  # The table's own note is not a machine.
  run qa_machine_env _
  [ "$status" -ne 0 ]
}

@test "every profile exports only strings and unsets only names" {
  # A value with a space is one export; a name with a space is not a name.
  jq -e '
    to_entries | map(select(.key != "_")) | all(
      (.value.env // {} | to_entries | all(.value | type == "string")) and
      (.value.unset // [] | all(test("^[A-Z_][A-Z0-9_]*$")))
    )' "$GOTG_DATA/qa-machines.json"
}

@test "qa refuses a bad --machine before it downloads anything" {
  gotg qa usa.nothing --machine moon
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no such machine profile: moon"* ]]
}

@test "the tools use nixpkgs' mesa only where the host has none" {
  # The game follows the machine profile through its own launcher; cage,
  # its Xwayland and the recorder follow the host. A profile cannot conjure
  # a GPU: on an NVIDIA desktop nixpkgs' mesa drives nothing, and a
  # compositor handed it recorded twenty seconds of one frame.
  GOTG_HOST_GL="$TEST_TMP/no-such-driver" run qa_host_lacks_gl
  [ "$status" -eq 0 ]
  mkdir -p "$TEST_TMP/opengl-driver"
  GOTG_HOST_GL="$TEST_TMP/opengl-driver" run qa_host_lacks_gl
  [ "$status" -ne 0 ]
}

@test "a machine's shims stand in for a tool, first on the game's PATH" {
  # The Deck's xrandr describes a portrait panel shown rotated; a frame sized
  # from its starred mode came up on its side, and only a Deck says that.
  run qa_machine_env deck "$TEST_TMP/machine-bin"
  [ "$status" -eq 0 ]
  [ -x "$TEST_TMP/machine-bin/xrandr" ]
  [[ "$output" == *"export PATH="*"machine-bin"* ]]
  local seen
  seen="$(bash -c "$(qa_machine_env deck "$TEST_TMP/machine-bin"); xrandr --current | head -2")"
  [[ "$seen" == *"current 1280 x 800"* ]]
  [[ "$seen" == *"eDP-1 connected primary 1280x800+0+0 right"* ]]
}

@test "asked for no directory, a profile installs no shims and says nothing about PATH" {
  run qa_machine_env deck
  [ "$status" -eq 0 ]
  [[ "$output" != *"PATH"* ]]
}

@test "a shim the profile names must exist" {
  cp "$GOTG_DATA/qa-machines.json" "$TEST_TMP/machines.json"
  jq '.deck.shims += ["nonesuch"]' "$TEST_TMP/machines.json" >"$TEST_TMP/m2.json"
  qa_machines_json() { printf '%s' "$TEST_TMP/m2.json"; }
  run --separate-stderr qa_machine_env deck "$TEST_TMP/machine-bin"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no file: qa-shims/deck/nonesuch"* ]]
}

@test "a profile can name a shim by path, for a program whose own PATH prefix would win" {
  run qa_machine_env deck "$TEST_TMP/machine-bin"
  [ "$status" -eq 0 ]
  [[ "$output" == *"export SPLITSCREEN_XRANDR='$TEST_TMP/machine-bin/xrandr'"* ]]
}

@test "a Deck carries no desktop's compositor preferences into the game" {
  # WLR_RENDERER=vulkan from a desktop's own sway reached the split-screen
  # frame under the deck profile, and with the Deck's GL -- nixpkgs' mesa,
  # no NVIDIA driver -- the nested sway could find no Vulkan device and
  # never came up. A Deck sets no such thing; neither does the profile.
  run qa_machine_env deck
  [[ "$output" == *"unset WLR_RENDERER"* ]]
}
