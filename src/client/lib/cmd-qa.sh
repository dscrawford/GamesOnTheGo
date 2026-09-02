# shellcheck shell=bash
# qa — run a game headlessly for a minute and grade the recording.
#
# The session is the fake gaming rig: a virtual pad that exists before the
# emulator does, a null audio sink recorded from its monitor, and a headless
# cage compositor recorded by wf-recorder. The grading lives in qa-analyze.sh;
# this file only assembles the rig, runs the game, and tears it down.
#
# Nothing here touches the machine's real launch state: GOTG_ENV_STATE_DIR is
# pointed into the run directory before play_prepare, so settings, bindings,
# adopted saves and pulled saves all land in scratch — a QA run must never be
# able to push button-mash progress over a person's save.

qa_tools_root() { printf '%s/qa/tools' "$GOTG_STATE_DIR"; }

qa_tools_ensure() {
  local root
  root="$(qa_tools_root)"
  if [[ ! -x "$root/bin/cage" ]]; then
    local flake
    flake="$(gotg_flake)"
    log "building QA tools from $flake#qa-tools"
    "$(nix_bin)" build "$flake#qa-tools" -o "$root" ||
      die "could not build qa-tools from $flake"
  fi
  export PATH="$root/bin:$PATH"
}

qa_golden_path() {
  local platform="$1" id="$2"
  printf '%s/qa/golden/%s/%s.png' "$GOTG_STATE_DIR" "$platform" "$id"
}

# The rig must not outlive the run, however the run ends.
qa_cleanup() {
  [[ -n "${QA_PAD_PID:-}" ]] && kill "$QA_PAD_PID" 2>/dev/null
  [[ -n "${QA_PAD_PID:-}" ]] && wait "$QA_PAD_PID" 2>/dev/null
  [[ -n "${QA_ROUTER_PID:-}" ]] && kill "$QA_ROUTER_PID" 2>/dev/null
  [[ -n "${QA_SINK_MODULE:-}" ]] && pactl unload-module "$QA_SINK_MODULE" 2>/dev/null
  return 0
}

# Is $1 a descendant of $2, by walking /proc ppids.
qa_pid_under() {
  local pid="$1" root="$2"
  while [[ -n "$pid" && "$pid" != 0 && "$pid" != 1 ]]; do
    [[ "$pid" == "$root" ]] && return 0
    pid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)" || return 1
  done
  return 1
}

# Herd the session's audio into the QA sink for as long as the run lasts.
#
# PULSE_SINK should be enough, and is not: pipewire's stream-restore remembers
# where an application played last and puts it back there — on this desktop
# that is the person's speakers, which both plays the run out loud and records
# silence. So every few seconds, any sink-input whose process lives under the
# session is moved onto the QA sink, whatever the server thinks it remembers.
qa_audio_route() {
  local rundir="$1" sink="$2"
  local session_pid=""
  while [[ -z "$session_pid" ]]; do
    sleep 1
    session_pid="$(cat "$rundir/session.pid" 2>/dev/null)" || session_pid=""
  done
  # The trailing || true is load-bearing: this subshell inherits set -e, and
  # the first sink-input that is NOT the session's would otherwise end the
  # whole router on the spot.
  while :; do
    pactl list sink-inputs 2>/dev/null |
      awk '/^Sink Input #/ {id=substr($3,2)} /application.process.id/ {gsub(/"/,"",$3); print id, $3}' |
      while read -r input pid; do
        if qa_pid_under "$pid" "$session_pid"; then
          # Volume too: the stream arrives with whatever level the server
          # remembers for this app, and the RMS check needs a known one.
          pactl move-sink-input "$input" "$sink" 2>/dev/null &&
            pactl set-sink-input-volume "$input" 100% 2>/dev/null &&
            printf 'moved sink-input %s (pid %s)\n' "$input" "$pid" >>"$rundir/audio-route.log" || true
        fi
      done
    sleep 2
  done
}

cmd_qa() {
  local want="" variant="" duration=60 boot_wait=15 bless=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h | --help)
        cat <<'EOF'
usage: gotg qa <id> [variant] [--duration N] [--boot-wait N] [--bless]

  Runs the game with no window, no speakers and no one holding the pad: a
  virtual controller mashes through it while the screen and the audio are
  recorded, then the recording is graded — did it boot, is there sound, did
  the inputs move anything, does the picture match the blessed frame.

    --duration N   seconds to run the game for (default 60)
    --boot-wait N  seconds before the pad starts pressing (default 15)
    --bless        store this run's reference frame as the golden image

  Artifacts and verdict.json land under ~/.local/state/gotg/qa/runs.
EOF
        return 0
        ;;
      --duration) duration="${2:-}"; shift 2 ;;
      --boot-wait) boot_wait="${2:-}"; shift 2 ;;
      --bless) bless=1; shift ;;
      -*) die "unknown option: $1 (gotg qa --help)" ;;
      *)
        if [[ -z "$want" ]]; then want="$1"
        elif [[ -z "$variant" ]]; then variant="$1"
        else die "unexpected argument: $1"; fi
        shift
        ;;
    esac
  done
  [[ -n "$want" ]] || die "usage: gotg qa <id> [variant] [--duration N] [--boot-wait N] [--bless]"
  [[ "$duration" =~ ^[0-9]+$ && "$boot_wait" =~ ^[0-9]+$ ]] ||
    die "--duration and --boot-wait take whole seconds"

  [[ -w /dev/uinput ]] ||
    die "cannot write /dev/uinput — the virtual pad needs it.
     Add yourself to the group that owns it (usually 'input') and log in again."

  qa_tools_ensure

  local rundir
  rundir="$GOTG_STATE_DIR/qa/runs/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$rundir/env-state"

  # Scratch launch state, set before play_prepare so every helper that derives
  # a path from env_state_dir agrees on it.
  export GOTG_ENV_STATE_DIR="$rundir/env-state"
  # No zenity: this is a terminal (or CI) workflow even when a display exists.
  export GOTG_NO_DIALOG=1

  # The pad first: pads_configure inside play_prepare must see it, and the
  # emulator must find it already present when SDL first scans /dev/input.
  # Not locals: the EXIT trap runs after this function's scope is gone.
  QA_PAD_PID="" QA_SINK_MODULE=""
  trap qa_cleanup EXIT

  local padlog="$rundir/pad.log"
  python3 "$GOTG_ROOT/qa/pad.py" --ready-file "$rundir/pad-ready" \
    --boot-wait "$boot_wait" >"$padlog" 2>&1 &
  QA_PAD_PID=$!
  local waited=0
  while [[ ! -e "$rundir/pad-ready" ]]; do
    kill -0 "$QA_PAD_PID" 2>/dev/null || die "virtual pad died: $(cat "$padlog")"
    sleep 0.2
    waited=$((waited + 1))
    [[ "$waited" -lt 50 ]] || die "virtual pad never came up: $(cat "$padlog")"
  done

  # Seat the virtual pad as player 1 for this run only. The machine's own
  # controllers.json stays untouched — it likely pins a real pad, and QA
  # binding a person's controller instead of its own is a test of nothing.
  # SDL renames the pad to its mapping's name, so it is found by the
  # vendor/product bytes in the GUID (045e/028e little-endian at the offsets
  # SDL puts them); ours is the newest device, so the last match wins over a
  # real wired pad with the same silicon.
  local pad_key
  pad_key="$("$(pads_bin)" 2>/dev/null |
    jq -r '[.[] | select(.identity | test("^0300....5e0400008e02"))][-1] | "\(.identity)/\(.slot)"')"
  if [[ -n "$pad_key" && "$pad_key" != "null/null" ]]; then
    jq -n --arg key "$pad_key" '{order: [$key]}' >"$rundir/controllers.json"
    export GOTG_PADS_ORDER_FILE="$rundir/controllers.json"
  else
    warn "could not find the virtual pad among SDL's controllers — bindings may go to a real one"
  fi

  # QA grades the current definition of the environment, not whichever build
  # happens to be rooted here — a stale root was the first bug a real run ever
  # caught. Same trade install makes: refresh when possible, run regardless.
  manifest_cached || manifest_ensure
  local attr
  attr="$(env_attr "$(manifest_find "$want")" "$variant")"
  if env_is_built "$attr"; then
    env_refresh "$attr" || warn "could not rebuild $attr — grading the build already here"
  fi

  play_prepare "$want" "$variant"
  local id platform
  id="$(manifest_field "$PLAY_GAME" id)"
  platform="$(manifest_field "$PLAY_GAME" platform)"

  # A null sink of our own: the run is silent in the room, and the monitor
  # source is the recording. Unique per run so two runs cannot cross-record.
  QA_SINK_MODULE="$(pactl load-module module-null-sink "sink_name=gotgqa$$" rate=48000)" ||
    die "could not create a null audio sink (is pipewire-pulse or pulseaudio running?)"

  # parecord, not ffmpeg's pulse input: measured on this pipewire, ffmpeg
  # attaches to a monitor source and captures pure zeros while parecord hears
  # it fine. Runs until the session is over and it is stopped.
  parecord --device="gotgqa$$.monitor" --file-format=wav "$rundir/audio.wav" &
  local audio_pid=$!

  qa_audio_route "$rundir" "gotgqa$$" &
  QA_ROUTER_PID=$!

  log "running $(manifest_field "$PLAY_GAME" title) headless for ${duration}s"
  # SDL_AUDIODRIVER (and SDL3's spelling): route SDL-audio emulators through
  # libpulse, the one backend that honors PULSE_SINK — SDL's native-pipewire
  # pick plays to the person's speakers and records nothing here. The outer
  # timeout is the backstop for a session whose teardown wedges; the session
  # owns the graceful path.
  local cage_status=0
  # GOTG_FULLSCREEN=0: with no tty the wrappers default to fullscreen, and
  # the fullscreen handoff through cage's XWayland WM is a coin toss — when it
  # loses, the viewport comes up black and stays black. Windowed is the one
  # behavior that lands every time, and a capture with the emulator's chrome
  # in it grades the same.
  env -u DISPLAY -u WAYLAND_DISPLAY \
    WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 \
    GOTG_FULLSCREEN=0 \
    PULSE_SINK="gotgqa$$" SDL_AUDIODRIVER=pulseaudio SDL_AUDIO_DRIVER=pulseaudio \
    GOTG_QA_DIR="$rundir" GOTG_QA_DURATION="$duration" \
    timeout -k 10 "$((duration + 90))" \
    cage -- "$GOTG_ROOT/qa/session.sh" "$(env_bin "$PLAY_ATTR")" "$PLAY_TARGET" \
    >"$rundir/session.log" 2>&1 || cage_status=$?

  kill -INT "$audio_pid" 2>/dev/null || true
  wait "$audio_pid" 2>/dev/null || true

  [[ -f "$rundir/status" ]] ||
    die "the session never ran the emulator (cage exited $cage_status) — see $rundir/session.log"

  # The golden frame, if this game has one, or this run's frame becoming it.
  # Taken near the end of the run — the most-progressed, most-settled screen —
  # and only compared against a run with the same timings, because the frame a
  # game shows at second N is a function of when the pad started pressing.
  local golden
  golden="$(qa_golden_path "$platform" "$id")"
  if [[ -n "$bless" ]]; then
    mkdir -p "$(dirname "$golden")"
    qa_frame "$rundir/video.mkv" "$((duration - 2))" "$golden"
    jq -n --argjson d "$duration" --argjson b "$boot_wait" \
      '{duration: $d, boot_wait: $b}' >"$golden.json"
    log "blessed: $golden"
  fi
  if [[ -f "$golden" ]]; then
    if [[ "$(jq -c . "$golden.json" 2>/dev/null)" == "$(jq -nc --argjson d "$duration" --argjson b "$boot_wait" '{duration: $d, boot_wait: $b}')" ]]; then
      cp "$golden" "$rundir/golden.png"
    else
      warn "golden for $id was blessed with different timings — graphics not graded (re-bless, or match its --duration/--boot-wait)"
    fi
  fi

  local verdict_status=0
  qa_verdict "$rundir" "$boot_wait" "$duration" || verdict_status=$?

  log ""
  log "run:     $rundir"
  jq -r '.checks | to_entries[] |
    "  " + (.key + "        " | .[0:10]) +
    (if .value.pass == true then "pass" elif .value.pass == false then "FAIL" else "skip" end)' \
    "$rundir/verdict.json" >&2
  if [[ "$verdict_status" -eq 0 ]]; then
    success "pass: $id"
  else
    log "$(jq -c '.checks' "$rundir/verdict.json")"
    die "FAIL: $id — captures and verdict.json are in $rundir"
  fi
}
