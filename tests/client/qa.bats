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
