#!/usr/bin/env bats
# `gotg steam` — putting a game in Steam's own shortcut file.
#
# The file is Steam's, not ours: it holds every non-Steam game the user has, so
# the things worth pinning are that an existing entry survives, that adding the
# same game twice does not duplicate it, and that a removal leaves the rest
# readable — Steam keys entries by position and drops everything after a gap.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  add_game n64 usa.legend_of_zelda_majoras_mask.z64 "rom" "Majora's Mask"
  # Sunshine, because it is the game that actually has variants.
  add_game gamecube usa.super_mario_sunshine.rvz "iso" "Super Mario Sunshine"

  export SHORTCUTS="$TEST_TMP/steam/userdata/1234/config/shortcuts.vdf"
  mkdir -p "$(dirname "$SHORTCUTS")"

  # The helper straight out of the package, and a Steam that is never running.
  export GOTG_STEAM_HELPER="$(dirname "$GOTG_BIN")/../share/gotg/steam/shortcuts.py"
}

teardown() { stop_saves_service; }

helper() { python3 "$GOTG_STEAM_HELPER" --file "$SHORTCUTS" "$@"; }

# An entry in exactly the shape Steam writes, quoting and all.
seed_existing() {
  helper add --name "Someone Else's Game" --exe "/opt/other/game.sh" \
    --start-dir "/opt/other" >/dev/null
}

@test "an added shortcut has the fields Steam writes" {
  run helper add --name "Majora's Mask" --exe "/home/x/Games/n64/play.sh" \
    --start-dir "/home/x/Games/n64"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action": "added"'* ]]

  run helper list
  [ "$(jq -r '.[0].name' <<<"$output")" = "Majora's Mask" ]
  # Steam stores the executable quoted; list strips it back off.
  [ "$(jq -r '.[0].exe' <<<"$output")" = "/home/x/Games/n64/play.sh" ]
}

@test "an icon given to add is stored on the shortcut and survives an update" {
  helper add --name "Zelda" --exe "/games/play.sh" --start-dir "/games" \
    --icon "/grid/123_icon.ico" >/dev/null
  run helper list
  [ "$(jq -r '.[0].icon' <<<"$output")" = "/grid/123_icon.ico" ]

  # An update that says nothing about the icon must not blank it.
  helper add --name "Zelda HD" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  run helper list
  [ "$(jq -r '.[0].icon' <<<"$output")" = "/grid/123_icon.ico" ]
}

@test "adding the same game twice updates rather than duplicating" {
  helper add --name "Old Name" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  helper add --name "New Name" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  run helper list
  [ "$(jq 'length' <<<"$output")" -eq 1 ]
  [ "$(jq -r '.[0].name' <<<"$output")" = "New Name" ]
}

@test "the appid follows the name, as Steam ROM Manager and EmuDeck do" {
  # It is computed from exe and name rather than kept, which is the convention
  # those tools set so artwork can be filed under a predictable id. The cost is
  # that renaming a game is a new id — they accept that and re-place the art.
  # Unchanged inputs give an unchanged id, which is the property that matters:
  # see steam-conventions.bats.
  run helper add --name "First" --exe "/games/play.sh" --start-dir "/games"
  local first
  first="$(jq -r '.appid' <<<"$output")"
  run helper add --name "Second" --exe "/games/play.sh" --start-dir "/games"
  [ "$(jq -r '.appid' <<<"$output")" != "$first" ]

  run helper add --name "Second" --exe "/games/play.sh" --start-dir "/games"
  local again
  again="$(jq -r '.appid' <<<"$output")"
  run helper add --name "Second" --exe "/games/play.sh" --start-dir "/games"
  [ "$(jq -r '.appid' <<<"$output")" = "$again" ]
}

@test "shortcuts that are not ours are left alone" {
  seed_existing
  helper add --name "Ours" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  run helper list
  [ "$(jq 'length' <<<"$output")" -eq 2 ]
  [[ "$output" == *"Someone Else's Game"* ]]
}

@test "removing one leaves the others readable" {
  seed_existing
  helper add --name "Ours" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  helper remove --exe "/games/play.sh" >/dev/null

  run helper list
  [ "$(jq 'length' <<<"$output")" -eq 1 ]
  [ "$(jq -r '.[0].name' <<<"$output")" = "Someone Else's Game" ]
}

@test "removing something absent is not an error" {
  seed_existing
  run helper remove --exe "/nothing/here.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"action": "absent"'* ]]
  run helper list
  [ "$(jq 'length' <<<"$output")" -eq 1 ]
}

@test "the previous file is kept before every write" {
  seed_existing
  helper add --name "Ours" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  [ -f "$(dirname "$SHORTCUTS")/shortcuts.vdf.gotg-bak" ]
}

@test "a first shortcut works with no file there at all" {
  [ ! -e "$SHORTCUTS" ]
  run helper add --name "First" --exe "/games/play.sh" --start-dir "/games"
  [ "$status" -eq 0 ]
  run helper list
  [ "$(jq 'length' <<<"$output")" -eq 1 ]
}

@test "gotg steam add writes the launcher and registers it" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam add usa.legend_of_zelda_majoras_mask
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"added"* ]]
  [ -x "$GOTG_GAMES_DIR/n64/play-usa.legend_of_zelda_majoras_mask.sh" ]
}

@test "a variant gets its own launcher and its own entry" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam add usa.super_mario_sunshine bse
  [ "$status" -eq 0 ]
  [ -x "$GOTG_GAMES_DIR/gamecube/play-usa.super_mario_sunshine-bse.sh" ]
  # Named so the two are told apart in a library list.
  [[ "$stderr" == *"(bse)"* ]]
}

@test "a variant and the plain game are separate entries" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam add usa.super_mario_sunshine
  gotg steam add usa.super_mario_sunshine bse
  run helper list
  [ "$(jq 'length' <<<"$output")" -eq 2 ]
}

@test "a variant that does not exist is refused before Steam is touched" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam add usa.super_mario_sunshine nosuchvariant
  [ "$status" -ne 0 ]
  [ ! -e "$SHORTCUTS" ]
}
