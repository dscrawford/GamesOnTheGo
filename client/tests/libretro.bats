#!/usr/bin/env bats
# Artwork from libretro-thumbnails, which needs no API key.
#
# SteamGridDB is the better source when it has the game and you have a key, but
# it needs a key, and Nintendo has had a good deal of Switch-era artwork taken
# down from it. libretro-thumbnails needs nothing, and is keyed by No-Intro
# filenames — which is how this library is already named, so the matching is
# tractable rather than a guess.
#
# There is no API: the directory index *is* the search. So most of what can go
# wrong here is name matching, and that is what most of these are about.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  export GRID="$TEST_TMP/grid"
  mkdir -p "$GRID"
  export ART="$(dirname "$GOTG_BIN")/../share/gotg/steam/artwork.py"
  start_libretro
}

teardown() { stop_libretro; }

start_libretro() {
  LR_PORT="$(pick_port)"
  python3 "$BATS_TEST_DIRNAME/mock_libretro.py" "$LR_PORT" "$@" &
  LR_PID=$!
  export LR_URL="http://127.0.0.1:$LR_PORT"
  local i
  for i in $(seq 1 50); do
    curl -s -o /dev/null "$LR_URL/" 2>/dev/null && return 0
    sleep 0.1
  done
  echo "mock libretro did not start" >&2
  return 1
}

stop_libretro() { [[ -n "${LR_PID:-}" ]] && kill "$LR_PID" 2>/dev/null || true; }
restart_libretro() {
  stop_libretro
  start_libretro "$@"
}

# No SteamGridDB at all: the point of this source is that it works without one.
art() {
  python3 "$ART" --grid-dir "$GRID" --appid 1234567890 \
    --name "Legend Of Zelda - Majora's Mask" \
    --id "usa.legend_of_zelda_majoras_mask" --platform n64 \
    --libretro-url "$LR_URL" "$@"
}

picked() { jq -r '.libretro.matched' <<<"$output"; }

# --- matching a No-Intro name -----------------------------------------------

@test "it finds a game whose article No-Intro moved to the end" {
  run art
  [ "$status" -eq 0 ]
  # "Legend Of Zelda - Majora's Mask" against "Legend of Zelda, The - ..."
  [[ "$(picked)" == "Legend of Zelda, The - Majora's Mask (USA)" ]]
}

@test "the region in the id decides which release is taken" {
  run python3 "$ART" --grid-dir "$GRID" --appid 1 \
    --name "Legend Of Zelda - Majora's Mask" \
    --id "jpn.legend_of_zelda_majoras_mask" --platform n64 --libretro-url "$LR_URL"
  [ "$status" -eq 0 ]
  [[ "$(picked)" == *"(Japan)"* ]]
}

@test "a world release prefers the NTSC box over the PAL one" {
  # A gotg "world." id is a multi-region cartridge, which No-Intro files as
  # "(Japan, USA)" or "(World)". Without a rule for it, Super Metroid took the
  # European box on a tie — a different box to the one on the shelf.
  run python3 "$ART" --grid-dir "$GRID" --appid 1 \
    --name "Legend Of Zelda - Majora's Mask" \
    --id "world.legend_of_zelda_majoras_mask" --platform n64 --libretro-url "$LR_URL"
  [ "$status" -eq 0 ]
  [[ "$(picked)" == *"(USA)"* ]]
}

@test "development builds are passed over for the real release" {
  run art
  # Asserted non-empty first: "nothing" is not "Debug" either, and a matcher
  # that found nothing would otherwise satisfy the two lines below.
  [ -n "$(picked)" ]
  [ "$(picked)" != "null" ]
  [[ "$(picked)" != *"Debug"* ]]
  [[ "$(picked)" != *"Rev 1"* ]]
}

@test "a game that exists only as a development build still gets its art" {
  run python3 "$ART" --grid-dir "$GRID" --appid 1 --name "Perfect Dark" \
    --id "usa.perfect_dark" --platform n64 --libretro-url "$LR_URL"
  [ "$status" -eq 0 ]
  # Ranked last, but last of one is still the pick: a picture beats no picture.
  [[ "$(picked)" == "Perfect Dark (USA) (Beta)" ]]
}

@test "a game the system does not list is reported, not invented" {
  run python3 "$ART" --grid-dir "$GRID" --appid 1 --name "Some Game Nobody Has" \
    --id "usa.some_game_nobody_has" --platform n64 --libretro-url "$LR_URL"
  [ "$status" -eq 0 ]
  [ "$(jq -r '.libretro.skipped' <<<"$output")" = "no match" ]
  [ -z "$(ls -A "$GRID")" ]
}

# --- which Steam filename each picture becomes ------------------------------

@test "a landscape box becomes the wide capsule, not the library tile" {
  run art
  [ "$status" -eq 0 ]
  # N64 boxes are landscape: as a 600x900 portrait tile it would be pillarboxed.
  [ -f "$GRID/1234567890.jpg" ]
  [ ! -f "$GRID/1234567890p.png" ]
}

@test "a portrait box becomes the library tile" {
  run python3 "$ART" --grid-dir "$GRID" --appid 55 --name "Super Mario 64" \
    --id "usa.super_mario_64" --platform n64 --libretro-url "$LR_URL"
  [ "$status" -eq 0 ]
  [ -f "$GRID/55p.png" ]
  [ ! -f "$GRID/55.jpg" ]
}

@test "the logo and a screenshot become the logo and the hero" {
  run art
  [ -f "$GRID/1234567890_logo.png" ]
  [ -f "$GRID/1234567890_hero.jpg" ]
}

@test "a kind the server does not have simply means fewer pictures" {
  restart_libretro --no-logos
  run art
  [ "$status" -eq 0 ]
  [ ! -f "$GRID/1234567890_logo.png" ]
  [ -f "$GRID/1234567890.jpg" ]
}

# --- the things that must never fail an add ---------------------------------

@test "a platform libretro does not carry is skipped, not an error" {
  run python3 "$ART" --grid-dir "$GRID" --appid 1 --name "Luigi's Mansion 2 HD" \
    --id "world.luigis_mansion_2_hd" --platform switch --libretro-url "$LR_URL"
  [ "$status" -eq 0 ]
  [[ "$(jq -r '.libretro.skipped' <<<"$output")" == *"switch"* ]]
}

@test "an unreachable server is not a failed add" {
  stop_libretro
  run art
  [ "$status" -eq 0 ]
  [[ "$(jq -r '.libretro.skipped' <<<"$output")" != "null" ]]
}

@test "existing artwork is left alone unless asked" {
  printf 'mine' >"$GRID/1234567890.jpg"
  run art
  [ "$(cat "$GRID/1234567890.jpg")" = "mine" ]
  run art --force
  [ "$(cat "$GRID/1234567890.jpg")" != "mine" ]
}

# --- alongside SteamGridDB --------------------------------------------------

@test "with no SteamGridDB key it still fetches what libretro has" {
  run art
  [ "$status" -eq 0 ]
  [ "$(jq -r '.skipped' <<<"$output")" = "no api key" ]
  [ "$(jq -r '.libretro.downloaded | length' <<<"$output")" -gt 0 ]
}

@test "libretro fills only what SteamGridDB did not" {
  SGDB_PORT="$(pick_port)"
  python3 "$BATS_TEST_DIRNAME/mock_steamgriddb.py" "$SGDB_PORT" &
  local sgdb=$!
  local i
  for i in $(seq 1 50); do
    curl -s -o /dev/null "http://127.0.0.1:$SGDB_PORT/img/probe" 2>/dev/null && break
    sleep 0.1
  done

  run art --base-url "http://127.0.0.1:$SGDB_PORT" --api-key testkey
  kill "$sgdb" 2>/dev/null || true
  [ "$status" -eq 0 ]

  # SteamGridDB had all five, so libretro had nothing left to add.
  [ "$(jq -r '.downloaded | length' <<<"$output")" -eq 5 ]
  [ "$(jq -r '.libretro.downloaded | length' <<<"$output")" -eq 0 ]
}
