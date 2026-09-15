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

@test "set-icon touches only the icon — name and appid survive" {
  helper add --name "Steam's Own Name" --exe "/games/play.sh" --start-dir "/games" >/dev/null
  local before
  before="$(helper list | jq -r '.[0].appid')"

  helper set-icon --exe "/games/play.sh" --icon "/grid/9_icon.ico" >/dev/null
  run helper list
  [ "$(jq -r '.[0].icon' <<<"$output")" = "/grid/9_icon.ico" ]
  [ "$(jq -r '.[0].name' <<<"$output")" = "Steam's Own Name" ]
  [ "$(jq -r '.[0].appid' <<<"$output")" = "$before" ]
}

@test "set-icon on a shortcut that is not there is a no-op" {
  run helper set-icon --exe "/nope.sh" --icon "/grid/9_icon.ico"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"absent"'* ]]
}

@test "set-icon rides Steam's own fields through untouched" {
  helper add --name "Zelda" --exe "/games/play.sh" --start-dir "/games" \
    --tag n64 >/dev/null
  python3 - "$SHORTCUTS" <<'EOF'
import sys, vdf
with open(sys.argv[1], "rb") as f:
    data = vdf.binary_load(f)
for e in data["shortcuts"].values():
    e["IsHidden"] = 1
    e["LastPlayTime"] = 1700000000
with open(sys.argv[1], "wb") as f:
    vdf.binary_dump(data, f)
EOF

  helper set-icon --exe "/games/play.sh" --icon "/grid/123_icon.ico" >/dev/null
  run helper list
  [ "$(jq -r '.[0].icon' <<<"$output")" = "/grid/123_icon.ico" ]
  [ "$(jq -r '.[0].tags | join(",")' <<<"$output")" = "n64" ]
  python3 - "$SHORTCUTS" <<'EOF'
import sys, vdf
with open(sys.argv[1], "rb") as f:
    e = list(vdf.binary_load(f)["shortcuts"].values())[0]
assert e["IsHidden"] == 1, e
assert e["LastPlayTime"] == 1700000000, e
EOF
}

@test "steam_icon_path files under the unsigned 32-bit appid, whichever spelling" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  load_client_libs
  source "$GOTG_LIB/cmd-steam.sh"

  local pair got want
  for pair in \
    "0:0" \
    "2147483647:2147483647" \
    "4294967295:4294967295" \
    "-1:4294967295" \
    "-1874645127:2420322169"; do
    got="$(steam_icon_path "${pair%%:*}")"
    want="$(dirname "$SHORTCUTS")/grid/${pair##*:}_icon.ico"
    [ "$got" = "$want" ] || {
      echo "appid ${pair%%:*}: got $got, want $want" >&2
      false
    }
  done
  # A vdf is another tool's to write too: a non-numeric appid must come back
  # empty, never reach arithmetic.
  [ -z "$(steam_icon_path 'x[$(true)]')" ]
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

@test "a variant environment's own title names the Steam entry, and says which mod" {
  # A Steam library sorts by name, so a mod sits beside the game it is a mod
  # of and has to say which mod it is. The environment chooses the words; the
  # parentheses are not its to forget.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/gamecube"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.hd.nix"
  fake_env env-gamecube-usa_super_mario_sunshine-hd "[]" "[]" "Sunshine HD Remaster"

  gotg steam add usa.super_mario_sunshine hd
  [ "$status" -eq 0 ]
  run helper list
  [ "$(jq -r '.[0].name' <<<"$output")" = "Sunshine HD Remaster (hd)" ]
}

@test "a title that already names the mod is not made to say it twice" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/gamecube"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.hd.nix"
  fake_env env-gamecube-usa_super_mario_sunshine-hd "[]" "[]" "Super Mario Sunshine (hd)"

  gotg steam add usa.super_mario_sunshine hd
  [ "$status" -eq 0 ]
  run helper list
  [ "$(jq -r '.[0].name' <<<"$output")" = "Super Mario Sunshine (hd)" ]
}

@test "a variant without a title of its own keeps the parenthesised default" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/gamecube"
  : >"$GOTG_ENV_DIR/games/gamecube/usa.super_mario_sunshine.hd.nix"
  fake_env env-gamecube-usa_super_mario_sunshine-hd

  gotg steam add usa.super_mario_sunshine hd
  [ "$status" -eq 0 ]
  run helper list
  [ "$(jq -r '.[0].name' <<<"$output")" = "Super Mario Sunshine (hd)" ]
}

@test "a variant that does not exist is refused before Steam is touched" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam add usa.super_mario_sunshine nosuchvariant
  [ "$status" -ne 0 ]
  [ ! -e "$SHORTCUTS" ]
}

# --- a change asked for while Steam is running ------------------------------
#
# Queued rather than refused, and applied once Steam is gone; cmd-steam.sh says
# why it cannot simply be written. These pin the queue's own behaviour: what is
# validated up front, what survives, and what is dropped.

pending_file() { printf '%s/steam-pending.json' "$GOTG_STATE_DIR"; }

@test "adding while Steam runs writes nothing and says why" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"waiting for it"* ]]
  [[ "$stderr" == *"restarted"* ]]
  [ ! -f "$SHORTCUTS" ]
  [ "$(jq -r '.[0].id' "$(pending_file)")" = "usa.legend_of_zelda_majoras_mask" ]
  [ "$(jq -r '.[0].op' "$(pending_file)")" = "add" ]
}

@test "the queue is applied by the next command once Steam is closed" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  [ "$status" -eq 0 ]

  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$status" -eq 0 ]
  [ "$(helper list | jq -r '.[0].name')" = "Majora's Mask" ]
  [ ! -f "$(pending_file)" ]
}

@test "a typo is refused while Steam runs rather than queued" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add not_a_game
  [ "$status" -ne 0 ]
  [ ! -f "$(pending_file)" ]
}

@test "asking twice queues one change, and the last word wins" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  [ "$(jq -r 'length' "$(pending_file)")" = "1" ]

  GOTG_STEAM_RUNNING=1 gotg steam remove usa.legend_of_zelda_majoras_mask
  [ "$(jq -r 'length' "$(pending_file)")" = "1" ]
  [ "$(jq -r '.[0].op' "$(pending_file)")" = "remove" ]
}

@test "a variant is queued as its own entry, not as the game" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.super_mario_sunshine
  GOTG_STEAM_RUNNING=1 gotg steam add usa.super_mario_sunshine bse
  [ "$(jq -r 'length' "$(pending_file)")" = "2" ]

  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$(helper list | jq -r 'length')" = "2" ]
}

@test "pending says what is waiting, and says so when nothing is" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam pending
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing is waiting"* ]]

  GOTG_STEAM_RUNNING=1 gotg steam add usa.super_mario_sunshine bse
  GOTG_STEAM_RUNNING=1 gotg steam pending
  [ "$status" -eq 0 ]
  [[ "$output" == *"add  usa.super_mario_sunshine bse"* ]]
}

@test "help does not empty the queue" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.super_mario_sunshine
  GOTG_STEAM_RUNNING=0 gotg steam help
  [ "$status" -eq 0 ]
  [ -s "$(pending_file)" ]
}

@test "a queued entry that stopped being addable does not jam the queue" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.super_mario_sunshine
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask

  # Poison the first one by taking its catalog entry away.
  jq '.games |= map(select(.id != "usa.super_mario_sunshine"))' \
    "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.t" && mv "$GOTG_CACHE_FILE.t" "$GOTG_CACHE_FILE"

  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$status" -eq 0 ]
  [ ! -f "$(pending_file)" ]
  [ "$(helper list | jq -r '.[0].name')" = "Majora's Mask" ]
}

# --- a queue file that is not what steam_queue left there --------------------

@test "queuing over a file that is no longer JSON says so instead of losing it" {
  # The one failure this feature cannot afford: a change accepted with a
  # reassuring paragraph and written nowhere.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  mkdir -p "$GOTG_STATE_DIR"
  printf '{ this is not json' >"$(pending_file)"

  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no longer readable as JSON"* ]]
  [[ "$stderr" != *"waiting for it"* ]]
}

@test "an unreadable queue is discarded out loud, not quietly" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  mkdir -p "$GOTG_STATE_DIR"
  printf '{ this is not json' >"$(pending_file)"

  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing readable"* ]]
  [ ! -f "$(pending_file)" ]
}

@test "a queue holding something that is not a record does not take the command down" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  jq '. + [5]' "$(pending_file)" >"$(pending_file).t" && mv "$(pending_file).t" "$(pending_file)"

  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$status" -eq 0 ]
  [ "$(helper list | jq -r 'length')" = "1" ]
}

# --- what the queue does with two requests about one game -------------------

@test "artwork queued behind an add does not replace it" {
  # Keyed on the operation as well as the game: these are two things to do, not
  # one person changing their mind, and dropping the add left the art to fail
  # for a game that was never put in Steam.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  GOTG_STEAM_RUNNING=1 gotg steam art usa.legend_of_zelda_majoras_mask
  [ "$(jq -r 'length' "$(pending_file)")" = "2" ]
  [ "$(jq -r '.[0].op' "$(pending_file)")" = "add" ]
  [ "$(jq -r '.[1].op' "$(pending_file)")" = "art" ]
}

@test "a remove cancels an add that has not happened yet" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  GOTG_STEAM_RUNNING=1 gotg steam remove usa.legend_of_zelda_majoras_mask
  [ "$(jq -r 'length' "$(pending_file)")" = "1" ]
  [ "$(jq -r '.[0].op' "$(pending_file)")" = "remove" ]
}

# --- art, which cannot always wait ------------------------------------------

@test "a typo in steam art is refused while Steam runs, as it is for add" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam art not_a_game_at_all
  [ "$status" -ne 0 ]
  [ ! -f "$(pending_file)" ]
}

@test "a picture chosen by hand is refused rather than queued" {
  # The queue remembers the game and the variant and nothing else, so a queued
  # --from would come back as whatever the search finds — which is not what was
  # asked for, and worse than saying no.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam art usa.legend_of_zelda_majoras_mask --from /tmp/mine.png
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"cannot wait for Steam"* ]]
  [ ! -f "$(pending_file)" ]
}

@test "a variant that could never name a launcher is refused by remove and art" {
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  gotg steam remove usa.super_mario_sunshine ../../../etc/passwd
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid variant name"* ]]

  gotg steam art usa.super_mario_sunshine ../../../etc/passwd
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid variant name"* ]]
}

# --- the queue outlives what goes wrong around it ---------------------------

@test "a flush that cannot reach a catalog keeps the queue for next time" {
  # manifest_ensure dies when there is no cache and nothing answers — an
  # ordinary offline moment — and it used to die after the file was deleted.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  [ -s "$(pending_file)" ]

  # No cache, and the service moved: manifest_ensure has nowhere to go.
  rm -f "$GOTG_CACHE_FILE"
  cp "$GOTG_CONFIG_DIR/api.json" "$TEST_TMP/api.json.real"
  jq '.url = "http://127.0.0.1:1"' "$TEST_TMP/api.json.real" >"$GOTG_CONFIG_DIR/api.json"

  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$status" -ne 0 ]
  [ -s "$(pending_file)" ]

  cp "$TEST_TMP/api.json.real" "$GOTG_CONFIG_DIR/api.json"
  GOTG_STEAM_RUNNING=0 gotg steam list
  [ "$status" -eq 0 ]
  [ "$(helper list | jq -r 'length')" = "1" ]
}

@test "two adds at once both survive" {
  # A read-modify-write with no lock reproducibly lost one of these.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 "$GOTG_BIN" steam add usa.legend_of_zelda_majoras_mask >/dev/null 2>&1 &
  local first=$!
  GOTG_STEAM_RUNNING=1 "$GOTG_BIN" steam add usa.super_mario_sunshine >/dev/null 2>&1 &
  local second=$!
  wait "$first"
  wait "$second"

  jq -e '.' "$(pending_file)" >/dev/null
  [ "$(jq -r 'length' "$(pending_file)")" = "2" ]
}

@test "an unknown subcommand still empties the queue before it complains" {
  # Deliberate: only the help spellings are exempt, and a typo is not one.
  export GOTG_STEAM_SHORTCUTS="$SHORTCUTS"
  GOTG_STEAM_RUNNING=1 gotg steam add usa.legend_of_zelda_majoras_mask
  GOTG_STEAM_RUNNING=0 gotg steam bogus-command
  [ "$status" -ne 0 ]
  [ ! -f "$(pending_file)" ]
  [ "$(helper list | jq -r 'length')" = "1" ]
}

