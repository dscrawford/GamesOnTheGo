#!/usr/bin/env bats
# `gotg configure storage` — several games directories, on several devices.
#
# Every directory is searched for what is installed; the first is where the
# next download lands; the device's free space is the only limit.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  # The suite pins one games directory in the environment; these tests are
  # about the configured list, so the pin comes off and HOME is sandboxed.
  export HOME="$TEST_TMP/home"
  mkdir -p "$HOME"
  unset GOTG_GAMES_DIR GOTG_PARTIAL_DIR
  export CARD="$TEST_TMP/card/Games"
}

teardown() { stop_saves_service; }

@test "with nothing configured the one directory is ~/Games, and it is the default" {
  gotg configure storage
  [ "$status" -eq 0 ]
  [[ "$output" == "* $HOME/Games"* ]]
  [[ "$stderr" == *"downloads land"* ]]
}

@test "adding a directory keeps the one already there, and creates the new one" {
  gotg configure storage add "$CARD"
  [ "$status" -eq 0 ]
  [ -d "$CARD" ]
  [[ "$stderr" == *"added: $CARD"* ]]
  [[ "$stderr" == *"downloads still land in $HOME/Games"* ]]

  gotg configure storage list
  [[ "${lines[0]}" == "* $HOME/Games"* ]]
  [[ "${lines[1]}" == "  $CARD"* ]]
  run jq -r '.games_dirs | join(":")' "$GOTG_CONFIG_FILE"
  [ "$output" = "$HOME/Games:$CARD" ]
}

@test "the json listing carries the default flag and the device's room" {
  gotg configure storage add "$CARD"
  gotg configure storage list --json
  [ "$status" -eq 0 ]
  run jq -r '.[0].default, .[1].default, .[1].path, (.[1].total_bytes > 0), (.[1].free_bytes <= .[1].total_bytes)' <<<"$output"
  [ "${lines[0]}" = true ]
  [ "${lines[1]}" = false ]
  [ "${lines[2]}" = "$CARD" ]
  [ "${lines[3]}" = true ]
  [ "${lines[4]}" = true ]
}

@test "default moves a directory first, and downloads land there" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  gotg configure storage add "$CARD"
  gotg configure storage default "$CARD"
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"downloads now land in $CARD"* ]]
  run jq -r '.games_dirs[0]' "$GOTG_CONFIG_FILE"
  [ "$output" = "$CARD" ]

  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ -f "$CARD/n64/usa.zelda.z64" ]
  [ ! -e "$HOME/Games/n64/usa.zelda.z64" ]
}

@test "a game in a directory that is not the default is still installed, launched and removed there" {
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  gotg configure storage add "$CARD"
  gotg configure storage default "$CARD"
  gotg download usa.zelda
  [ -f "$CARD/n64/usa.zelda.z64" ]

  # The disk becomes the default again; the card still holds the game.
  gotg configure storage default "$HOME/Games"
  gotg list --installed
  [[ "$output" == *"[*]"*"usa.zelda"* ]]
  gotg complete installed
  [[ "$output" == *"n64/usa.zelda"* ]]
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already installed: $CARD/n64/usa.zelda.z64"* ]]
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64 launched with: $CARD/n64/usa.zelda.z64"* ]]
  gotg install usa.zelda
  [ "$status" -eq 0 ]
  [ -x "$CARD/n64/play-usa.zelda.sh" ]
  [ ! -e "$HOME/Games/n64/play-usa.zelda.sh" ]

  gotg uninstall usa.zelda
  [ "$status" -eq 0 ]
  [ ! -e "$CARD/n64/usa.zelda.z64" ]
  [ ! -e "$CARD/n64/play-usa.zelda.sh" ]
}

@test "remove forgets a directory and deletes nothing in it" {
  gotg configure storage add "$CARD"
  printf 'keep' >"$CARD/keep"
  gotg configure storage remove "$CARD"
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing in it was deleted"* ]]
  [ -f "$CARD/keep" ]
  gotg configure storage list
  [[ "$output" != *"$CARD"* ]]
}

@test "the last directory cannot be removed, a relative path is refused, and a repeat is refused" {
  gotg configure storage remove "$HOME/Games"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"last games directory"* ]]
  gotg configure storage add "relative/path"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"absolute path"* ]]
  gotg configure storage add "$CARD"
  gotg configure storage add "$CARD"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"already a games directory"* ]]
  gotg configure storage remove "$TEST_TMP/nowhere"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not a games directory"* ]]
}

@test "a relative entry somebody wrote into the config is ignored, not resolved" {
  mkdir -p "$GOTG_CONFIG_DIR"
  chmod 700 "$GOTG_CONFIG_DIR"
  jq -n --arg c "$CARD" '{games_dirs: ["relative", $c]}' >"$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"
  mkdir -p "$CARD"
  gotg configure storage list
  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == "* $CARD"* ]]
  [[ "$output" != *"relative"* ]]
}

@test "GOTG_GAMES_DIR in the environment is the only directory, whatever the config says" {
  gotg configure storage add "$CARD"
  export GOTG_GAMES_DIR="$TEST_TMP/pinned"
  mkdir -p "$GOTG_GAMES_DIR"
  gotg configure storage list
  [[ "${lines[0]}" == "* $GOTG_GAMES_DIR"* ]]
  [[ "$output" != *"$CARD"* ]]
}

@test "help and an unknown subcommand" {
  gotg configure storage --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"where downloads land"* ]]
  gotg configure storage bogus
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg configure storage"* ]]
}
