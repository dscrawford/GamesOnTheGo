#!/usr/bin/env bats
# The conventions other tools that write shortcuts.vdf follow, and why.
#
# Steam ROM Manager and EmuDeck both write a *deterministic* appid, computed
# from the executable and the name rather than picked at random. That is not
# decoration: every piece of artwork for a non-Steam game is filed under that
# id, in userdata/<user>/config/grid, so an id that changes between runs orphans
# the art. Steam's own "Add a Non-Steam Game" does pick randomly, which is why
# an entry it created cannot be reproduced by the formula — the tools use it so
# they can put artwork in place without asking Steam anything.
#
#   Steam ROM Manager, src/lib/helpers/steam/generate-app-id.ts
#     top  = crc32(exe + appname) | 0x80000000
#     long = (top << 32) | 0x02000000        <- Big Picture grid filename
#     appid in shortcuts.vdf = (long >> 32) - 0x100000000
#
#   EmuDeck, tools/vdf/add.py
#     appid = generate_short_app_id(target_path, name)
#     tags  = {"0": ...}                     <- becomes a Steam collection
#
# These tests hold us to the same, so artwork can be added later without every
# id moving underneath it.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
  add_game gamecube usa.super_mario_sunshine.rvz "iso" "Super Mario Sunshine"

  export SHORTCUTS="$TEST_TMP/steam/userdata/1234/config/shortcuts.vdf"
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  mkdir -p "$(dirname "$SHORTCUTS")"
  export GOTG_STEAM_HELPER="$(dirname "$GOTG_BIN")/../share/gotg/steam/shortcuts.py"
}

teardown() { stop_server; }

helper() { python3 "$GOTG_STEAM_HELPER" --file "$SHORTCUTS" "$@"; }

# The reference implementation, transcribed from Steam ROM Manager.
expected_appid() {
  python3 -c "
import binascii, sys
exe, name = sys.argv[1], sys.argv[2]
top = binascii.crc32((exe + name).encode()) | 0x80000000
print((((top << 32) | 0x02000000) >> 32) - 0x100000000)
" "$1" "$2"
}

@test "the appid is the one Steam ROM Manager and EmuDeck would compute" {
  run helper add --name "Super Mario Sunshine" --exe "/games/play.sh" --start-dir "/games"
  [ "$status" -eq 0 ]
  local want
  want="$(expected_appid "/games/play.sh" "Super Mario Sunshine")"
  [ "$(jq -r '.appid' <<<"$output")" = "$want" ]
}

@test "the same game always gets the same appid, so artwork stays attached" {
  run helper add --name "Ours" --exe "/games/play.sh" --start-dir "/games"
  local first="$(jq -r '.appid' <<<"$output")"
  helper remove --exe "/games/play.sh" >/dev/null
  run helper add --name "Ours" --exe "/games/play.sh" --start-dir "/games"
  [ "$(jq -r '.appid' <<<"$output")" = "$first" ]
}

@test "the long form is available too, for the Big Picture grid" {
  run helper add --name "Ours" --exe "/games/play.sh" --start-dir "/games"
  # (short << 32) | 0x02000000 — the filename Big Picture artwork uses.
  local short long
  short="$(jq -r '.appid' <<<"$output")"
  long="$(jq -r '.grid_appid' <<<"$output")"
  [ "$long" = "$(python3 -c "print((($short + 0x100000000) << 32) | 0x02000000)")" ]
}

@test "an entry is tagged with its platform, so Steam can group them" {
  gotg steam add usa.super_mario_sunshine
  [ "$status" -eq 0 ]
  run helper list
  [ "$(jq -r '.[0].tags | join(",")' <<<"$output")" = "gamecube" ]
}

@test "the name is the catalog's title, not the id and not the file name" {
  gotg steam add usa.super_mario_sunshine
  run helper list
  [ "$(jq -r '.[0].name' <<<"$output")" = "Super Mario Sunshine" ]
}

@test "a variant says so in the name, and is a different appid" {
  gotg steam add usa.super_mario_sunshine
  gotg steam add usa.super_mario_sunshine bse
  run helper list
  [ "$(jq -r '[.[].name] | sort | join("|")' <<<"$output")" = "Super Mario Sunshine|Super Mario Sunshine (bse)" ]
  [ "$(jq -r '[.[].appid] | unique | length' <<<"$output")" -eq 2 ]
}
