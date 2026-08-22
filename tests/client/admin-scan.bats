#!/usr/bin/env bats
# `gotg admin scan`: what the library gained since the last look, and which
# games the bytes have gone from under.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
}

teardown() { stop_saves_service; }

admin() {
  GOTG_ADMIN_TOKEN="admin-token" gotg admin "$@"
}

# The mark is what makes a bare run mean "since I last looked", so most of
# these have to get past a first run before they are testing anything.
mark_taken() {
  settled
  admin scan
  [ "$status" -eq 0 ]
}

# imported_at is a whole second, and a scan leaves its own second for the next
# call rather than reporting a second it may not have seen all of. So a test
# that adds games and scans inside one second is asking about a window that
# has not closed yet — which is correct, and not what the test means to check.
settled() { sleep 1; }

@test "the first scan sets the mark instead of declaring the whole library new" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  add_game ps2 usa.battlefront_2.iso rom "Battlefront II"

  admin scan
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"first scan"* ]]
  [[ "$stderr" == *"2 game(s)"* ]]
  [ -f "$GOTG_STATE_DIR/admin-scan.json" ]
}

@test "a second scan names what arrived between the two" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken

  add_game gb usa.super_mario_land.gb rom "Super Mario Land"
  add_game ps2 usa.battlefront_2.iso rom "Battlefront II"
  settled

  admin scan
  [ "$status" -eq 0 ]
  [[ "$output" == *"+ gb"*"usa.super_mario_land"*"Super Mario Land"* ]]
  [[ "$output" == *"+ ps2"*"usa.battlefront_2"*"Battlefront II"* ]]
  # Already reported once, and reported once is the whole point of the mark.
  [[ "$output" != *"usa.mother_3"* ]]
  [[ "$stderr" == *"2 added, 0 missing"* ]]
}

@test "a scan with nothing new since the last one says so and lists nothing" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken

  admin scan
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"0 added, 0 missing"* ]]
}

@test "bytes gone from under a row are reported, and the row is left alone" {
  add_game snes usa.chrono_trigger.sfc rom "Chrono Trigger"
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken

  rm "$SERVICE_LIBRARY_DIR/snes/usa.chrono_trigger.sfc"

  admin scan
  [ "$status" -eq 0 ]
  [[ "$output" == *"- snes"*"usa.chrono_trigger"*"Chrono Trigger"* ]]
  [[ "$output" == *"1 of 1 file(s) gone"* ]]
  [[ "$output" != *"usa.mother_3"* ]]
  [[ "$stderr" == *"0 added, 1 missing"* ]]

  # Reporting, not deleting: a row with no file behind it is a person's
  # decision to make, so the catalog still names it afterwards.
  admin scan --json
  [ "$(jq -r '.total' <<<"$output")" = "2" ]
}

@test "a missing game keeps being reported until somebody deals with it" {
  add_game snes usa.chrono_trigger.sfc rom "Chrono Trigger"
  mark_taken
  rm "$SERVICE_LIBRARY_DIR/snes/usa.chrono_trigger.sfc"

  admin scan
  [[ "$output" == *"usa.chrono_trigger"* ]]
  # The mark has moved past it and it is still gone. A vanish is a state, not
  # an event, so the next run must not fall silent about it.
  admin scan
  [[ "$output" == *"usa.chrono_trigger"* ]]
}

@test "--since asks about a window without moving the mark" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken
  add_game ps2 usa.battlefront_2.iso rom "Battlefront II"
  settled

  admin scan --since 1h
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.mother_3"* ]]
  [[ "$output" == *"usa.battlefront_2"* ]]

  # The mark is where the first scan left it, so the plain run still has news.
  admin scan
  [[ "$output" == *"usa.battlefront_2"* ]]
  [[ "$output" != *"usa.mother_3"* ]]
}

@test "--all counts the whole catalog as new and leaves the mark alone" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken

  admin scan --all
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.mother_3"* ]]

  admin scan
  [ -z "$output" ]
}

@test "--json is the report itself, for anything that is not a person" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken
  add_game ps2 usa.battlefront_2.iso rom "Battlefront II"
  settled

  admin scan --json
  [ "$status" -eq 0 ]
  [ "$(jq -r '.added | length' <<<"$output")" = "1" ]
  [ "$(jq -r '.added[0].id' <<<"$output")" = "usa.battlefront_2" ]
  [ "$(jq -r '.total' <<<"$output")" = "2" ]
  [[ "$(jq -r '.scanned_at' <<<"$output")" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]]
}

@test "a whole library that stopped being there says so rather than just listing it" {
  local i
  for i in 1 2 3 4 5; do
    add_game gba "usa.game_$i.gba" rom "Game $i"
  done
  mark_taken
  rm -r "$SERVICE_LIBRARY_DIR/gba"

  admin scan
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"volume that failed to mount"* ]]
  [[ "$stderr" == *"0 added, 5 missing"* ]]
}

@test "a --since nobody can parse is refused before it reaches the service" {
  mark_taken

  admin scan --since yesterday
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"12h, 7d, 2w"* ]]

  admin scan --since
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--since needs a time"* ]]
}

@test "an unknown option is an error, not a silently different scan" {
  admin scan --everything
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"unknown option for scan"* ]]
}

@test "the scan needs the admin token like the rest of admin does" {
  add_game gba usa.mother_3.gba rom "Mother 3"

  GOTG_ADMIN_TOKEN="" gotg admin scan
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"GOTG_ADMIN_TOKEN"* ]]

  GOTG_ADMIN_TOKEN="test-token" gotg admin scan
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"the service answered 403"* ]]

  # A run that was refused must not move the mark and claim it looked.
  [ ! -e "$GOTG_STATE_DIR/admin-scan.json" ]
}

@test "scan is in the admin help" {
  admin help
  [[ "$output" == *"scan"* ]]
  [[ "$output" == *"--since"* ]]
}

# --- what the review pass found -----------------------------------------------

@test "--all and --since together is a refusal, not a silent preference" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken

  admin scan --all --since 1h
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"name different windows"* ]]

  admin scan --since 1h --all
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"name different windows"* ]]
}

@test "a mark this command cannot have written does not wedge every run after it" {
  add_game gba usa.mother_3.gba rom "Mother 3"
  mark_taken

  local bad
  for bad in "yesterday" "1700000000" "2026-01-01" "not json at all" "{}"; do
    if [[ "$bad" == "{}" || "$bad" == "not json at all" ]]; then
      printf '%s' "$bad" >"$GOTG_STATE_DIR/admin-scan.json"
    else
      jq -n --arg t "$bad" '{scanned_at: $t}' >"$GOTG_STATE_DIR/admin-scan.json"
    fi

    admin scan
    [ "$status" -eq 0 ] || {
      echo "wedged on mark: $bad -> $stderr" >&2
      false
    }
    # It never reaches the service as a --since nobody validated…
    [[ "$stderr" != *"the service answered"* ]]
    # …it says which file was unreadable, rather than silently starting over…
    [[ "$stderr" == *"unreadable"* ]]
    # …and it heals, so the next run is an ordinary one.
    [[ "$(jq -r '.scanned_at' "$GOTG_STATE_DIR/admin-scan.json")" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]]
  done
}

@test "escape sequences in a title never reach the terminal" {
  # The service checks isprintable() on the way in, so this has to be put in
  # behind its back — which is the point: the endpoint this must survive is a
  # compromised one, and jq's @tsv escapes tab and newline but not ESC.
  add_game gba usa.mother_3.gba rom "Mother 3"
  local esc
  esc="$(printf '\033')"
  python3 - "$TEST_TMP/service-state/catalog.db" <<'EOF'
import sqlite3, sys

hostile = "\x1b]52;c;AAAA\x07\x1b[2K Mother 3"
conn = sqlite3.connect(sys.argv[1], timeout=10)
conn.execute("UPDATE entry SET title = ? WHERE id = 'usa.mother_3'", (hostile,))
conn.commit()
EOF
  settled

  admin scan --all
  [ "$status" -eq 0 ]
  [[ "$output" != *"$esc"* ]] || {
    echo "an escape sequence reached the terminal" >&2
    false
  }
  # The title is still readable, minus what a terminal would have acted on.
  [[ "$output" == *"Mother 3"* ]]
}

@test "a multibyte title survives the escape filter intact" {
  # The reason this is filtered in jq and not with `printable`: tr -cd
  # '[:print:]' under the C locale eats every multibyte character, and a
  # Japanese dump name is a real name.
  add_game gba usa.mother_3.gba rom "マザー3"
  settled

  admin scan --all
  [ "$status" -eq 0 ]
  [[ "$output" == *"マザー3"* ]]
}
