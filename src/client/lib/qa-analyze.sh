# shellcheck shell=bash
# Grading a QA run's captures: is there sound, is there picture, did the
# scripted inputs visibly move anything. Everything here works on files a
# session already wrote, so all of it is testable against synthesized media
# with known defects — see tests/client/qa.bats.
#
# Every threshold is an environment variable with a default, because they are
# guesses tuned against real captures, not derivations.

# ffmpeg's detectors report to stderr as log lines; that text is the
# interface. Two quirks matter and are handled here:
#   - silencedetect closes an interval at EOF, freezedetect does not — a game
#     frozen until the end emits only freeze_start, and the tail has to be
#     closed against the clip duration.
#   - astats reports "-inf" for digital silence, which is not a JSON number.

qa_media_duration() {
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"
}

# {rms_db, silence_total, longest_silence, longest_silence_in_window,
#  duration, window_start}
#
# Silence is graded over the input window for the reason black is: a game's
# first seconds are a logo screen with nothing to hear, and on the Deck
# Animal Crossing spends about twelve of them there -- long enough to fail a
# run whose sound was fine the moment it started. The whole-capture numbers
# stay for the eye; the in-window one is what the verdict grades.
#
# The bar that goes with it is 15 seconds rather than 10, which is where the
# measurement put it: the attract loop in Melee cycles title, fade, demo,
# and its quiet stretches come in at 9.5 to 10.8 seconds depending on where
# a run lands in the cycle. At 10 the same healthy game passed on one
# machine and failed on the other. A check that flips a coin measures
# nothing, and a game with no sound at all is caught by the other half of
# this rule, where RMS across the whole capture has to clear -45 dB.
qa_audio_stats() {
  local wav="$1" window_start="${2:-0}" lines windowed dur
  dur="$(qa_media_duration "$wav")"
  lines="$(ffmpeg -hide_banner -nostats -i "$wav" \
    -af "silencedetect=n=${GOTG_QA_SILENCE_DB:--50dB}:d=0.5,astats=metadata=0" \
    -f null - 2>&1)"
  windowed="$(ffmpeg -hide_banner -nostats -ss "$window_start" -i "$wav" \
    -af "silencedetect=n=${GOTG_QA_SILENCE_DB:--50dB}:d=0.5" \
    -f null - 2>&1)"

  # The last "RMS level dB" is astats' Overall section, after the per-channel
  # ones.
  local rms
  rms="$(awk -F': ' '/RMS level dB/ {v=$2} END {print (v == "" || v == "-inf") ? -120 : v}' <<<"$lines")"

  local total longest
  total="$(awk -F'silence_duration: ' '/silence_duration/ {s+=$2} END {printf "%.3f", s}' <<<"$lines")"
  longest="$(awk -F'silence_duration: ' '/silence_duration/ {if ($2>m) m=$2} END {printf "%.3f", m}' <<<"$lines")"

  local in_window
  in_window="$(awk -F'silence_duration: ' \
    '/silence_duration/ {if ($2>m) m=$2} END {printf "%.3f", m}' <<<"$windowed")"

  jq -n --argjson rms "$rms" --argjson total "$total" \
    --argjson longest "$longest" --argjson in_window "$in_window" \
    --argjson dur "$dur" --argjson window "$window_start" \
    '{rms_db: $rms, silence_total: $total, longest_silence: $longest,
      longest_silence_in_window: $in_window, duration: $dur,
      window_start: $window}'
}

# {black_total, freeze_in_window, duration, window_start}
#
# Freeze is only measured from window_start on — the scripted inputs begin
# there, and a title screen sitting still before them is not a defect. The
# same measurement is the controller check: inputs that reach the game move
# pixels.
qa_video_stats() {
  local video="$1" window_start="$2" dur
  dur="$(qa_media_duration "$video")"

  # Black is graded over the input window, not the whole capture: a real game
  # spends its first seconds on black boot and logo screens (an N64 title takes
  # a while to render its first frame), and only black *once the game should be
  # showing something and taking input* is a rendering fault. black_total is
  # kept for the eye; black_in_window is what the verdict grades.
  local black_total black_in_window
  black_total="$(ffmpeg -hide_banner -nostats -i "$video" \
    -vf "blackdetect=d=0.5:pix_th=${GOTG_QA_BLACK_PIX_TH:-0.10}" -f null - 2>&1 |
    awk -F'black_duration:' '/black_duration/ {s+=$2} END {printf "%.3f", s+0}')"
  black_in_window="$(ffmpeg -hide_banner -nostats -i "$video" \
    -vf "trim=start=$window_start,setpts=PTS-STARTPTS,blackdetect=d=0.5:pix_th=${GOTG_QA_BLACK_PIX_TH:-0.10}" \
    -f null - 2>&1 |
    awk -F'black_duration:' '/black_duration/ {s+=$2} END {printf "%.3f", s+0}')"

  local freeze_lines window_dur freeze
  freeze_lines="$(ffmpeg -hide_banner -nostats -i "$video" \
    -vf "trim=start=$window_start,setpts=PTS-STARTPTS,freezedetect=n=${GOTG_QA_FREEZE_DB:--60dB}:d=2" \
    -f null - 2>&1 | grep -F 'freezedetect' || true)"
  window_dur="$(jq -n --argjson d "$dur" --argjson w "$window_start" '$d - $w')"
  freeze="$(awk -F': ' -v total="$window_dur" '
    /freeze_start/ {start=$2; open=1}
    /freeze_duration/ {s+=$2; open=0}
    END {if (open) s+=total-start; printf "%.3f", s}' <<<"$freeze_lines")"

  jq -n --argjson black "$black_total" --argjson blackw "$black_in_window" \
    --argjson freeze "$freeze" --argjson dur "$dur" --argjson w "$window_start" \
    '{black_total: $black, black_in_window: $blackw, freeze_in_window: $freeze, duration: $dur, window_start: $w}'
}

qa_frame() {
  local video="$1" at="$2" out="$3"
  ffmpeg -hide_banner -loglevel error -y -ss "$at" -i "$video" -frames:v 1 "$out"
}

# ImageMagick's phash metric: 0 for identical, coefficient distance otherwise.
# The scale is not a Hamming distance — same scene lands near zero, a
# different scene lands in the tens of thousands. compare exits 1 to say
# "different", which is an answer, not a failure.
qa_phash() {
  local a="$1" b="$2" out
  out="$(magick compare -metric phash "$a" "$b" null: 2>&1)" || [[ $? -eq 1 ]]
  awk '{print $1}' <<<"$out"
}

# Whether the overlay's bar is in `after` and was not in `before`: a JSON
# object with `pass`.
#
# Read where gotg-killswitch draws it (scene.c: the bar is 8.5% of the
# screen's height, 48 to 128 px, the exit ring in its middle) as one
# signature: a band down the left of the bar that is near-black, and a middle
# that is mostly near-black with red in it -- the ring and its X, part way
# closed while the virtual pad holds the chord. A red ring on a dark bar, in the frame itself. It has to be
# there once the chord is held and not before, so a game whose top edge is
# dark, or red, cannot pass on its own; comparing the red *gained* instead
# failed on a stand-in game that was red all over. What it cannot tell apart:
# a game's own dark band with a red HUD element that appears exactly between
# the two frames sampled. The overlay log says which process drew, for a run
# where that matters.
qa_overlay_signature() {
  local frame="$1" size w h bar band middle
  size="$(magick identify -format '%w %h' "$frame" 2>/dev/null)" || {
    printf '{"present": false}\n'
    return 0
  }
  read -r w h <<<"$size"
  bar=$(((h * 85 + 500) / 1000))
  ((bar >= 48)) || bar=48
  ((bar <= 128)) || bar=128
  band="$((w / 4))x$((bar - 8))+8+4"
  middle="$((bar * 2))x${bar}+$((w / 2 - bar))+0"
  local dark red middle_dark
  dark="$(qa_region_mean "$frame" "$band" 'max(r,max(g,b))')"
  red="$(qa_region_mean "$frame" "$middle" '(r>0.55)*(g<0.35)*(b<0.35)')"
  middle_dark="$(qa_region_mean "$frame" "$middle" 'max(r,max(g,b))<0.3')"
  jq -n --argjson dark "${dark:-1}" --argjson red "${red:-0}" --argjson middle_dark "${middle_dark:-0}" \
    '{present: ($dark <= 0.35 and $red >= 0.02 and $middle_dark >= 0.5),
      bar_band: $dark, ring_red: $red, middle_dark: $middle_dark}'
}

# The mean of an fx expression over one crop of a frame; empty if it cannot
# be read.
qa_region_mean() {
  magick "$1" -crop "$2" +repage -fx "$3" -format '%[fx:mean]' info: 2>/dev/null
}

qa_overlay_check() {
  qa_overlay_judge "$(qa_overlay_signature "$1")" "$(qa_overlay_signature "$2")"
}

# Two signatures, before and after, to a verdict.
qa_overlay_judge() {
  jq -n --argjson before "$1" --argjson after "$2" \
    '{pass: ($after.present and ($before.present | not)), before: $before, after: $after}'
}

# The overlay axis of a run that asked for it (`gotg qa --overlay-at N`): the
# exit ring is on screen from about N+3 to N+6 (qa/session.sh has the
# timeline), counted from a session start a moment after the recording's --
# so two frames from inside that window are tried, against one from before
# anybody joined.
qa_overlay_verdict() {
  local rundir="$1" at offset frame_at before result='{"pass": false, "why": "no frame from inside the overlay"}'
  at="$(jq -r '.overlay_at // empty' "$rundir/run.json" 2>/dev/null)"
  # Checked before it meets arithmetic: bash evaluates a variable's text in
  # $(( )), and this file could come from a run directory made elsewhere.
  if [[ ! "$at" =~ ^[0-9]+$ ]]; then
    printf '{"pass": false, "why": "overlay_at is not a number of seconds"}\n'
    return 0
  fi
  if ! qa_frame "$rundir/video.mkv" "$((10#$at > 1 ? 10#$at - 1 : 0))" "$rundir/overlay-before.png" 2>/dev/null; then
    printf '%s\n' "$result"
    return 0
  fi
  before="$(qa_overlay_signature "$rundir/overlay-before.png")"
  for offset in 4.8 5.4; do
    frame_at="$(awk -v a="$at" -v o="$offset" 'BEGIN { print a + o }')"
    qa_frame "$rundir/video.mkv" "$frame_at" "$rundir/overlay.png" 2>/dev/null || continue
    result="$(qa_overlay_judge "$before" "$(qa_overlay_signature "$rundir/overlay.png")")"
    [[ "$(jq -r .pass <<<"$result")" != true ]] || break
  done
  printf '%s\n' "$result"
}

# Grade a run directory — audio.wav, video.mkv, status, optionally golden.png
# — into verdict.json. Exit 0 only if every axis passed.
#
# expected_duration is how long the emulator ran. The recorder only receives
# frames when the screen changes, so a capture far shorter than the run means
# the screen sat still — an emulator stuck on a dialog looks exactly like
# this, and it was the first failure a real run produced. It fails the video
# axis, and it means the freeze window may lie beyond the capture, so the
# controller axis cannot silently pass off the back of an empty trim.
qa_verdict() {
  local rundir="$1" window_start="$2" expected_duration="$3"

  # A run ends by being stopped, so the stopping signals are how a healthy one
  # exits: 124 is timeout giving up, 143 the emulator taking the TERM, 137 the
  # KILL that follows for one that traps TERM to save on the way out (the
  # HarbourMasters ports do). 0 is a game that closed itself. Anything else —
  # a SIGSEGV, a non-zero return — is a crash, and fails to boot.
  local status boots
  status="$(cat "$rundir/status")"
  boots="$(jq -n --argjson s "$status" '$s == 0 or $s == 124 or $s == 143 or $s == 137')"

  local audio video
  audio="$(qa_audio_stats "$rundir/audio.wav" "$window_start")"
  video="$(qa_video_stats "$rundir/video.mkv" "$window_start")"

  # Graphics only grades against a golden frame, taken at the same offset
  # --bless takes it. No golden, no opinion: pass stays null.
  local graphics='{"pass": null}'
  if [[ -f "$rundir/golden.png" ]]; then
    local frame_at distance
    frame_at="$(jq -n --argjson d "$expected_duration" '$d - 2')"
    qa_frame "$rundir/video.mkv" "$frame_at" "$rundir/frame.png"
    distance="$(qa_phash "$rundir/frame.png" "$rundir/golden.png")"
    graphics="$(jq -n --argjson d "$distance" \
      --argjson max "${GOTG_QA_PHASH_MAX:-1000}" \
      '{pass: ($d <= $max), distance: $d}')"
  fi

  local overlay='{"pass": null}'
  [[ "$(jq -r '.overlay_at // empty' "$rundir/run.json" 2>/dev/null)" == "" ]] ||
    overlay="$(qa_overlay_verdict "$rundir")"

  jq -n \
    --argjson boots "$boots" --argjson status "$status" \
    --argjson audio "$audio" --argjson video "$video" \
    --argjson graphics "$graphics" --argjson overlay "$overlay" \
    --argjson rms_min "${GOTG_QA_RMS_MIN:--45}" \
    --argjson silence_max "${GOTG_QA_SILENCE_MAX:-15}" \
    --argjson black_frac_max "${GOTG_QA_BLACK_FRAC_MAX:-0.25}" \
    --argjson freeze_frac_max "${GOTG_QA_FREEZE_FRAC_MAX:-0.65}" \
    --argjson expected "$expected_duration" \
    '($video.duration >= $expected * 0.8) as $captured |
    # Frozen time as a share of the input window: splash and menu screens
    # legitimately sit still for seconds, so only a window that is MOSTLY
    # still — inputs moving nothing — is a controller failure.
    ($video.freeze_in_window / ([$expected - $video.window_start, 1] | max)) as $freeze_frac |
    # Black time as a share of the same window, for the same reason it is a
    # share for freeze. As a flat number of seconds it asked a 150-second run
    # and a 30-second one for the same budget, and the longer run failed for
    # being longer: the attract loop in Melee fades to black between demos, so
    # black time grows with the run while its share does not.
    ($video.black_in_window / ([$expected - $video.window_start, 1] | max)) as $black_frac |
    {
      checks: {
        boots: {pass: $boots, status: $status},
        audio: ($audio + {pass: ($audio.rms_db > $rms_min and $audio.longest_silence_in_window <= $silence_max)}),
        video: ($video + {expected_duration: $expected, black_frac: $black_frac,
                          pass: ($captured and $black_frac <= $black_frac_max)}),
        controller: {pass: ($captured and $freeze_frac <= $freeze_frac_max),
                     freeze_in_window: $video.freeze_in_window, freeze_frac: $freeze_frac},
        graphics: $graphics,
        overlay: $overlay
      }
    } | .pass = ([.checks[].pass] | all(. != false))' \
    >"$rundir/verdict.json.checks"
  # Which machine the run pretended to be, beside the checks: a pass on a
  # desktop and a fail as a Deck are two facts about one game.
  jq --arg m "$(cat "$rundir/machine" 2>/dev/null || echo desktop)" '. + {machine: $m}' \
    "$rundir/verdict.json.checks" >"$rundir/verdict.json"
  rm -f "$rundir/verdict.json.checks"

  jq -e '.pass' "$rundir/verdict.json" >/dev/null
}
