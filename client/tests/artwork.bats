#!/usr/bin/env bats
# Artwork for Steam entries, from SteamGridDB.
#
# The filenames are the whole contract: Steam looks for them by the shortcut's
# appid in userdata/<user>/config/grid, and a name it does not recognise is
# simply ignored. They are transcribed from EmuDeck's copy_steam_images(), which
# is the same set Steam ROM Manager writes:
#
#     <appid>.jpg        banner / wide capsule
#     <appid>p.png       portrait, the library tile
#     <appid>_hero.jpg   hero banner
#     <appid>_logo.png   logo overlay
#     <appid>_icon.ico   icon
#
# Fetching is best-effort throughout. A shortcut with no picture is a working
# shortcut; a failed download that took the shortcut with it would not be.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  export GRID="$TEST_TMP/grid"
  mkdir -p "$GRID"
  export ART="$(dirname "$GOTG_BIN")/../share/gotg/steam/artwork.py"
  start_sgdb
}

teardown() { stop_sgdb; }

start_sgdb() {
  SGDB_PORT="$(pick_port)"
  python3 "$BATS_TEST_DIRNAME/mock_steamgriddb.py" "$SGDB_PORT" "$@" &
  SGDB_PID=$!
  export SGDB_URL="http://127.0.0.1:$SGDB_PORT"
  local i
  for i in $(seq 1 50); do
    curl -s -o /dev/null "$SGDB_URL/img/probe" 2>/dev/null && return 0
    sleep 0.1
  done
  echo "mock steamgriddb did not start" >&2
  return 1
}

stop_sgdb() { [[ -n "${SGDB_PID:-}" ]] && kill "$SGDB_PID" 2>/dev/null || true; }
restart_sgdb() {
  stop_sgdb
  start_sgdb "$@"
}

art() {
  python3 "$ART" --grid-dir "$GRID" --appid 1234567890 --name "Some Game" \
    --base-url "$SGDB_URL" --api-key testkey "$@"
}

@test "it writes the five names Steam looks for" {
  run art
  [ "$status" -eq 0 ]
  [ -f "$GRID/1234567890.jpg" ]
  [ -f "$GRID/1234567890p.png" ]
  [ -f "$GRID/1234567890_hero.jpg" ]
  [ -f "$GRID/1234567890_logo.png" ]
  [ -f "$GRID/1234567890_icon.ico" ]
}

@test "the top pick is the highest scoring asset, not the first listed" {
  art
  # The mock lists a score-1 asset before a score-99 one.
  grep -q "best" "$GRID/1234567890p.png"
  ! grep -q "worse" "$GRID/1234567890p.png"
}

@test "it reports what it fetched" {
  run art
  [ "$(jq -r '.game' <<<"$output")" = "The Top Match" ]
  [ "$(jq -r '.downloaded | length' <<<"$output")" -eq 5 ]
}

@test "without an api key it skips rather than failing" {
  run python3 "$ART" --grid-dir "$GRID" --appid 1 --name "Some Game" --base-url "$SGDB_URL"
  [ "$status" -eq 0 ]
  [ "$(jq -r '.skipped' <<<"$output")" = "no api key" ]
  [ -z "$(ls -A "$GRID")" ]
}

@test "a key the server refuses is reported, not a crash" {
  restart_sgdb --reject-key
  run art
  [ "$status" -eq 0 ]
  [[ "$(jq -r '.skipped' <<<"$output")" == *"key"* ]]
}

@test "a title with no match leaves the grid alone" {
  restart_sgdb --no-match
  run art
  [ "$status" -eq 0 ]
  [ "$(jq -r '.skipped' <<<"$output")" = "no match" ]
  [ -z "$(ls -A "$GRID")" ]
}

@test "a game with no assets of a kind simply has fewer pictures" {
  restart_sgdb --no-assets
  run art
  [ "$status" -eq 0 ]
  [ "$(jq -r '.downloaded | length' <<<"$output")" -eq 0 ]
}

@test "existing artwork is left alone unless asked" {
  printf 'mine' >"$GRID/1234567890p.png"
  run art
  [ "$(cat "$GRID/1234567890p.png")" = "mine" ]
  run art --force
  [ "$(cat "$GRID/1234567890p.png")" != "mine" ]
}

@test "an unreachable server is not a failed add" {
  stop_sgdb
  run art
  [ "$status" -eq 0 ]
  [[ "$(jq -r '.skipped' <<<"$output")" != "null" ]]
}

# --- the command around it -------------------------------------------------

setup_steam() {
  start_server
  write_config
  add_game gamecube usa.super_mario_sunshine.rvz "iso" "Super Mario Sunshine"
  export SHORTCUTS="$TEST_TMP/steam/userdata/1234/config/shortcuts.vdf"
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  export GOTG_STEAM_ARTWORK="$(dirname "$GOTG_BIN")/../share/gotg/steam/artwork.py"
  mkdir -p "$(dirname "$SHORTCUTS")"
  export GOTG_STEAMGRIDDB_KEY_FILE="$TEST_TMP/steamgriddb.json"
}

@test "adding a game fetches its artwork" {
  setup_steam
  jq -n '{api_key: "testkey"}' >"$GOTG_STEAMGRIDDB_KEY_FILE"
  export GOTG_STEAMGRIDDB_URL="$SGDB_URL"
  gotg steam add usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"artwork: 5 file(s)"* ]]
  stop_server

  # Filed under the appid the shortcut carries, beside the shortcuts file —
  # which is what makes Steam find it.
  local appid
  appid="$(python3 "$(dirname "$GOTG_BIN")/../share/gotg/steam/shortcuts.py" \
    --file "$SHORTCUTS" list | jq -r '.[0].appid')"
  [ -f "$(dirname "$SHORTCUTS")/grid/${appid}p.png" ]
  [ -f "$(dirname "$SHORTCUTS")/grid/${appid}_hero.jpg" ]
}

@test "art refetches for a game already in Steam" {
  setup_steam
  jq -n '{api_key: "testkey"}' >"$GOTG_STEAMGRIDDB_KEY_FILE"
  export GOTG_STEAMGRIDDB_URL="$SGDB_URL"
  gotg steam add usa.super_mario_sunshine
  gotg steam art usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  # Already there, so nothing is refetched without --force.
  [[ "$stderr" == *"artwork: 0 file(s)"* ]]
  gotg steam art usa.super_mario_sunshine --force
  [[ "$stderr" == *"artwork: 5 file(s)"* ]]
  stop_server
}

@test "with no key, adding still works and says how to add one" {
  setup_steam
  gotg steam add usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"No SteamGridDB key"* ]]
  [[ "$stderr" == *"steamgriddb.json"* ]]
  stop_server
}

@test "art refuses for a game that is not in Steam yet" {
  setup_steam
  gotg steam art usa.super_mario_sunshine
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not in Steam yet"* ]]
  stop_server
}
