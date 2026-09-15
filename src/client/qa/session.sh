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

status=0
timeout -k 5 "$GOTG_QA_DURATION" "$@" || status=$?

# Before touching the recorder: the verdict needs this file even if the
# teardown below goes badly.
printf '%s\n' "$status" >"$GOTG_QA_DIR/status"

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
