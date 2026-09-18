# shellcheck shell=bash
# qa — run a game headlessly for a minute and grade the recording.
#
# Assembles a virtual pad, a null audio sink, and a headless cage compositor
# recorded by wf-recorder; qa-analyze.sh does the grading.
#
# GOTG_ENV_STATE_DIR is pointed into the run directory before play_prepare, so
# settings, bindings and saves all land in scratch — a QA run must never be
# able to push button-mash progress over a person's save.

qa_tools_root() { printf '%s/qa/tools' "$GOTG_STATE_DIR"; }

# Every binary a run needs, named once — also how a stale qa-tools root is
# detected: a root built before gotg-qa-python was split out passed a check
# for cage alone, then died on the virtual pad.
QA_TOOLS=(cage wf-recorder ffmpeg ffprobe magick parecord pactl gotg-qa-python)

qa_tools_have_all() {
  local prefix="${1:-}" tool
  for tool in "${QA_TOOLS[@]}"; do
    if [[ -n "$prefix" ]]; then
      [[ -x "$prefix/$tool" ]] || return 1
    else
      command -v "$tool" >/dev/null 2>&1 || return 1
    fi
  done
}

qa_tools_ensure() {
  # Already provided — the container image carries them, and a pod has no
  # writable nix store to build into anyway. Checked by asking for the tools
  # rather than for the GC root, so any way of supplying them counts.
  qa_tools_have_all && return 0

  local root
  root="$(qa_tools_root)"
  if ! qa_tools_have_all "$root/bin"; then
    local flake
    flake="$(gotg_flake)"
    log "building QA tools from $flake#qa-tools"
    "$(nix_bin)" build "$flake#qa-tools" -o "$root" ||
      die "could not build qa-tools from $flake"
  fi
  export PATH="$root/bin:$PATH"

  qa_tools_have_all ||
    die "qa-tools is missing something a run needs — rebuild it with:
     nix build $(gotg_flake)#qa-tools -o $root"
}

qa_golden_path() {
  local platform="$1" id="$2"
  printf '%s/qa/golden/%s/%s.png' "$GOTG_STATE_DIR" "$platform" "$id"
}

# Carry a game's ROM-derived assets into the run's isolated state.
#
# The HarbourMasters ports extract a multi-megabyte .o2r from the ROM on first
# run, behind a zenity "Generate now?" dialog drawn as a second window the
# headless kiosk cannot route input to. The archive depends on the ROM, not on
# play, so copying the machine's own into scratch skips the dialog without
# changing what is graded. None to copy means never bootstrapped —
# qa_require_bootstrap turns that into one clear sentence rather than a hang.
#
# scratch mirrors the real state dir's layout, <scratch>/<attr>, so a path
# relative to one maps straight onto the other.
qa_seed_bootstrap() {
  local attr="$1" scratch="$2" real o2r dest
  real="$GOTG_STATE_DIR/env/$attr"
  [[ -d "$real" ]] || return 0
  while IFS= read -r -d '' o2r; do
    dest="$scratch/$attr/${o2r#"$real"/}"
    mkdir -p "$(dirname "$dest")"
    cp -f "$o2r" "$dest"
  done < <(find "$real" -name '*.o2r' -print0 2>/dev/null)
}

# Stop before a run that is only going to hang. A harkinian environment whose
# first-run extraction has never happened cannot start unattended; say so, and
# say the one command that fixes it.
qa_require_bootstrap() {
  local attr="$1" scratch="$2"
  grep -q 'first run: extracting game assets' "$(env_bin "$attr")" 2>/dev/null || return 0
  find "$scratch" -name '*.o2r' -print -quit 2>/dev/null | grep -q . && return 0
  die "this game extracts its assets on first run, behind a dialog no headless
     run can answer. Bootstrap it once with a real window:
       gotg play ${attr#env-}
     answer the extraction prompt, quit, then QA will reuse what it made."
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
#
# ppid cannot be taken as a field number. Field two is the process name, it can
# hold spaces and parentheses, and a name is the process's own to choose — so
# everything up to the *last* ") " is pid and name, and ppid is what follows.
# Taking $4 instead reads the tail of the name: measured here, `npm exec
# open-w` answers "open-w)" and `mt76-tx phy1` answers "S". That fails the walk
# and the emulator's audio is never moved onto the QA sink, which grades as a
# silent run — the audio axis failing on a name it had no business reading.
#
# The numeric test is the other half: a name can be chosen to look like a pid,
# so anything that is not a number ends the walk rather than being followed.
# A run directory of its own, named for when it started. Made by mktemp, not
# by the timestamp alone: two Jobs sharing one state volume started in the
# same second and wrote one another's verdicts.
qa_new_rundir() {
  local runs="$GOTG_STATE_DIR/qa/runs"
  mkdir -p "$runs"
  mktemp -d "$runs/$(date +%Y%m%d-%H%M%S)-XXXX"
}

qa_pid_under() {
  local pid="$1" root="$2" stat rest
  while [[ "$pid" =~ ^[0-9]+$ && "$pid" != 0 && "$pid" != 1 ]]; do
    [[ "$pid" == "$root" ]] && return 0
    read -r stat <"/proc/$pid/stat" 2>/dev/null || return 1
    # What follows the last ") " is the state letter, then ppid.
    rest="${stat##*') '}"
    rest="${rest#* }"
    pid="${rest%% *}"
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
      awk '/^Sink Input #/ {id = substr($3, 2)}
           /^[[:space:]]*application\.process\.id[[:space:]]*=/ {gsub(/"/, "", $3); print id, $3}' |
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

# Where the machine profiles live: a table beside overrides.json, one entry
# per kind of machine a launch has to work on.
qa_machines_json() { printf '%s/qa-machines.json' "$GOTG_DATA"; }

qa_machine_names() { jq -r 'keys[] | select(. != "_")' "$(qa_machines_json)" | tr '\n' ' '; }

# The shell lines that turn this run into that machine, for the session to
# source right before it runs the game: `export` for what the profile sets,
# `unset` for what it takes away. Written rather than passed as one string
# because a value with a space in it is one export, not two.
qa_machine_env() {
  local machine="$1" file
  file="$(qa_machines_json)"
  jq -e --arg m "$machine" 'has($m) and $m != "_"' "$file" >/dev/null 2>&1 ||
    die "no such machine profile: $machine (known: $(qa_machine_names))"
  jq -r --arg m "$machine" '
    (.[$m].unset // [])[] | "unset " + .
  ' "$file"
  jq -r --arg m "$machine" '
    (.[$m].env // {}) | to_entries[] | "export " + .key + "=" + (.value | @sh)
  ' "$file"
}

# Whether the run's tools -- the compositor, its Xwayland, the recorder --
# should use nixpkgs' mesa: only on a host with no GL of its own. The game
# follows the machine profile through its own launcher; the harness around
# it follows the host, because a profile cannot conjure a GPU. Pretending
# to be a Deck on an NVIDIA desktop gave cage nixpkgs' mesa, which drives no
# NVIDIA card, and the harness recorded twenty seconds of one frame.
qa_host_lacks_gl() {
  [[ ! -e "${GOTG_HOST_GL:-/run/opengl-driver}" ]]
}

cmd_qa() {
  local want="" variant="" duration=60 boot_wait=15 bless="" machine="desktop"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h | --help)
        cat <<'EOF'
usage: gotg qa <id> [variant] [--duration N] [--boot-wait N] [--bless] [--machine M]

  Runs the game with no window, no speakers and no one holding the pad: a
  virtual controller mashes through it while the screen and the audio are
  recorded, then the recording is graded — did it boot, is there sound, did
  the inputs move anything, does the picture match the blessed frame.

    --duration N   seconds to run the game for (default 60)
    --boot-wait N  seconds before the pad starts pressing (default 15)
    --bless        store this run's reference frame as the golden image
    --machine M    pretend to be that machine: desktop (default), deck,
                   deck-desktop. A profile is the conditions that told a
                   machine apart when a launch worked here and failed there
                   — no host GL, X11 only, a C locale — applied to the game
                   and nothing else, so one box can stand in for several.

  Artifacts and verdict.json land under ~/.local/state/gotg/qa/runs.
EOF
        return 0
        ;;
      --duration) duration="${2:-}"; shift 2 ;;
      --boot-wait) boot_wait="${2:-}"; shift 2 ;;
      --bless) bless=1; shift ;;
      --machine) machine="${2:-}"; shift 2 ;;
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
  # Checked before anything is built or downloaded: a typo here is the
  # cheapest thing to be wrong about after the id.
  qa_machine_env "$machine" >/dev/null

  # The game first: an id that is not in the catalog is the cheapest thing
  # to be wrong about. A game imported since the cache was written is the
  # usual thing a QA run is for, so one refresh before giving up on it.
  manifest_cached || manifest_ensure
  local game
  if ! game="$(manifest_find "$want" 2>/dev/null)"; then
    manifest_refresh || true
    game="$(manifest_find "$want")"
  fi

  [[ -w /dev/uinput ]] ||
    die "cannot write /dev/uinput — the virtual pad needs it.
     Add yourself to the group that owns it (usually 'input') and log in again."

  qa_tools_ensure

  local rundir
  rundir="$(qa_new_rundir)"
  mkdir -p "$rundir/env-state"

  # Scratch launch state, set before play_prepare so every helper that derives
  # a path from env_state_dir agrees on it.
  export GOTG_ENV_STATE_DIR="$rundir/env-state"
  # No zenity: this is a terminal (or CI) workflow even when a display exists.
  export GOTG_NO_DIALOG=1
  # And no controller gate. Nobody is in front of this to hold a button, and
  # the gate waits for one until somebody does -- which on a machine with a
  # padmap daemon and an unmapped pad is a run that never starts and never
  # says why. QA brings its own virtual pad a few lines below; the question
  # the gate asks is already answered.
  export GOTG_SEAT_GATE=0

  # The pad first: pads_configure inside play_prepare must see it, and the
  # emulator must find it already present when SDL first scans /dev/input.
  # Not locals: the EXIT trap runs after this function's scope is gone.
  QA_PAD_PID="" QA_SINK_MODULE=""
  trap qa_cleanup EXIT

  # The QA python, not the client's: only one of them has evdev, and which
  # `python3` resolves to depends on how the two got onto PATH.
  local padlog="$rundir/pad.log"
  gotg-qa-python "$GOTG_ROOT/qa/pad.py" --ready-file "$rundir/pad-ready" \
    --boot-wait "$boot_wait" >"$padlog" 2>&1 &
  QA_PAD_PID=$!
  local waited=0
  while [[ ! -e "$rundir/pad-ready" ]]; do
    kill -0 "$QA_PAD_PID" 2>/dev/null || die "virtual pad died: $(cat "$padlog")"
    sleep 0.2
    waited=$((waited + 1))
    [[ "$waited" -lt 50 ]] || die "virtual pad never came up: $(cat "$padlog")"
  done

  # Seat the virtual pad as player 1 for this run only; the machine's own
  # controllers.json, likely pinned to a real pad, stays untouched. SDL renames
  # the pad to its mapping's name, so it is matched on the GUID's vendor and
  # product bytes instead (045e/028e little-endian); the last match wins,
  # taking ours over a real wired pad with the same silicon.
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
  local attr
  attr="$(env_attr "$game" "$variant")"
  if env_is_built "$attr"; then
    env_refresh "$attr" || warn "could not rebuild $attr — grading the build already here"
  fi

  play_prepare "$want" "$variant"
  local id platform
  id="$(manifest_field "$PLAY_GAME" id)"
  platform="$(manifest_field "$PLAY_GAME" platform)"

  qa_seed_bootstrap "$PLAY_ATTR" "$rundir/env-state"
  qa_require_bootstrap "$PLAY_ATTR" "$rundir/env-state"

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

  # The machine this run pretends to be. Applied by the session, to the game
  # alone: the recorder, the pad and cage itself stay what they are here --
  # except for GL, which the tools need too and a Deck does not have.
  if qa_host_lacks_gl; then
    log "the compositor and recorder use nixpkgs' mesa (no host GL)"
    eval "$(gotg-qa-gl-env)"
  fi
  qa_machine_env "$machine" >"$rundir/machine.env"
  printf '%s\n' "$machine" >"$rundir/machine"
  log "running $(manifest_field "$PLAY_GAME" title) headless for ${duration}s as a $machine"
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
  # WLR_RENDERER is inherited on purpose. It is a person's choice for their
  # own compositor, and on the one desktop this was measured on it is also
  # the choice that works: sway on Vulkan handed cage Vulkan, and cage on
  # NVIDIA records fine that way, where its own pick -- GLES2 -- recorded
  # twenty seconds of one frame. A machine with no such choice, the Deck,
  # lets cage pick, and it picks right there.
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
  log "machine: $machine"
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
