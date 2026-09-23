#!/usr/bin/env bash
# The inside of the QA compositor: everything that must see cage's
# WAYLAND_DISPLAY. Records the virtual output while the emulator runs for the
# allotted time, then writes how the emulator exited — 124 from timeout is the
# expected end of a run that had to be stopped.
#
# argv is the emulator command; where to write and how long to run arrive as
# GOTG_QA_DIR and GOTG_QA_DURATION, because cage passes its client's argv
# through untouched and mixing ours into it invites off-by-one grief.
set -euo pipefail

# For the audio router outside: everything under this pid is the session.
printf '%s\n' "$$" >"$GOTG_QA_DIR/session.pid"

wf-recorder -f "$GOTG_QA_DIR/video.mkv" &
recorder=$!

# The machine this run pretends to be, applied here and to the game alone:
# what the profile exports and what it takes away. The recorder above has
# already started with the real environment.
if [ -f "$GOTG_QA_DIR/machine.env" ]; then
  # shellcheck disable=SC1091
  . "$GOTG_QA_DIR/machine.env"
fi
# The overlay, asked for with --overlay-at N, driven the way a room drives
# it: at N a pad holds to join on a stand-in padmap socket (qa/padmap.py) and
# takes seat two at N+1.5, its badge up until N+3; at N+3 the virtual pad
# holds both shoulders and Start (qa/pad.py, told by the chord file) for three
# seconds of a four-second hold, so the exit ring is on screen from about N+3
# to N+6 and nothing is stopped. qa_overlay_verdict samples N+4.8 and N+5.4:
# change one side and change the other. The kill switch watches this shell,
# so it goes when the session does.
helpers=()
if [ -n "${GOTG_QA_OVERLAY_AT:-}" ]; then
  at="$GOTG_QA_OVERLAY_AT"
  socket="$GOTG_QA_DIR/padmap.sock"
  gotg-qa-python "$(dirname "$0")/padmap.py" --socket "$socket" --join-at "$at" \
    >"$GOTG_QA_DIR/padmap.log" 2>&1 &
  helpers+=($!)
  for _ in $(seq 1 50); do
    [ -S "$socket" ] && break
    sleep 0.1
  done
  GOTG_OVERLAY_PADMAP_SOCKET="$socket" PADMAP_HOLD_SECONDS=1.5 \
    "$GOTG_QA_KILLSWITCH" --pid "$$" --hold-ms 4000 >"$GOTG_QA_DIR/overlay.log" 2>&1 &
  (sleep "$((at + 3))" && touch "$GOTG_QA_DIR/chord") &
  helpers+=($!)
fi
status=0
timeout -k 5 "$GOTG_QA_DURATION" "$@" || status=$?

# Before touching the recorder: the verdict needs this file even if the
# teardown below goes badly.
printf '%s\n' "$status" >"$GOTG_QA_DIR/status"
# The stand-in padmap waits forever, and nothing else would stop it.
[ "${#helpers[@]}" -eq 0 ] || kill "${helpers[@]}" 2>/dev/null || true

# SIGINT is wf-recorder's clean stop — it finalizes the container on it. But
# on a headless output it can then sit waiting for a frame event that will
# never come now the emulator is gone, so a stop that outlives a short grace
# is escalated. The encoder has already flushed by then; the file survives.
kill -INT "$recorder" 2>/dev/null || true
for _ in $(seq 1 20); do
  kill -0 "$recorder" 2>/dev/null || break
  sleep 0.25
done
kill -KILL "$recorder" 2>/dev/null || true
wait "$recorder" 2>/dev/null || true
