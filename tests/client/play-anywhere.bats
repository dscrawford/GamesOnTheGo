#!/usr/bin/env bats
# `nix run <gotg>#play -- <attr>`: a game on a machine with a token and
# nothing else. The script, with a nix that records what it is asked.

bats_require_minimum_version 1.5.0

setup() {
  TMP="$BATS_TEST_TMPDIR"
  SCRIPT="${BATS_TEST_DIRNAME}/../../src/client/play-anywhere.sh"
  [ -f "$SCRIPT" ] || SCRIPT="$GOTG_PLAY_ANYWHERE"
  export GOTG_CONFIG_DIR="$TMP/config" GOTG_FLAKE="path:/nix/store/aaaa-source" HOME="$TMP/home"
  mkdir -p "$TMP/bin" "$HOME"
  NIX_CALLS="$TMP/nix-calls"
  : >"$NIX_CALLS"
  cat >"$TMP/bin/nix" <<SHIM
#!$(command -v bash)
printf '%s\n' "\$*" >>"$NIX_CALLS"
printf 'NIX_CONFIG=%s\n' "\${NIX_CONFIG:-}" >>"$NIX_CALLS"
SHIM
  chmod +x "$TMP/bin/nix"
  export PATH="$TMP/bin:$PATH"
}

play() { run --separate-stderr bash "$SCRIPT" "$@"; }

with_token() {
  mkdir -p "$GOTG_CONFIG_DIR"
  printf '{"url":"https://gotg.example/","token":"tok123"}\n' >"$GOTG_CONFIG_DIR/api.json"
}

@test "with a token, a library is made of its server and the game is run by the library's path" {
  with_token
  play n64.usa.donkey_kong_64 --windowed
  [ "$status" -eq 0 ]
  [ -f "$GOTG_CONFIG_DIR/library/flake.nix" ]
  grep -q 'gotg.url = "path:/nix/store/aaaa-source";' "$GOTG_CONFIG_DIR/library/flake.nix"
  grep -q 'url = "file+https://gotg.example/catalog";' "$GOTG_CONFIG_DIR/library/flake.nix"
  grep -q 'server = "https://gotg.example";' "$GOTG_CONFIG_DIR/library/flake.nix"
  grep -qx "run $GOTG_CONFIG_DIR/library#n64.usa.donkey_kong_64 -- --windowed" "$NIX_CALLS"
  [[ "$stderr" == *"made a library of https://gotg.example"* ]]
}

@test "the netrc is written for the server's host, private, and named to nix" {
  with_token
  play n64.usa.donkey_kong_64
  [ "$status" -eq 0 ]
  [ "$(stat -c '%a' "$GOTG_CONFIG_DIR/netrc")" = 600 ]
  grep -qx "machine gotg.example login gotg password tok123" "$GOTG_CONFIG_DIR/netrc"
  grep -q "NIX_CONFIG=netrc-file = $GOTG_CONFIG_DIR/netrc" "$NIX_CALLS"
}

@test "a second run makes nothing again" {
  with_token
  play n64.usa.donkey_kong_64
  play n64.usa.donkey_kong_64
  [ "$status" -eq 0 ]
  [[ "$stderr" != *"made a library"* ]]
  [ "$(grep -c "^machine" "$GOTG_CONFIG_DIR/netrc")" = 1 ]
}

@test "GOTG_TOKEN is a first contact: api.json is written as login would, 0600" {
  GOTG_TOKEN=tok456 GOTG_SERVER=https://games.example.org/ play usa.donkey_kong_64
  [ "$status" -eq 0 ]
  [ "$(stat -c '%a' "$GOTG_CONFIG_DIR/api.json")" = 600 ]
  [ "$(jq -r .url "$GOTG_CONFIG_DIR/api.json")" = "https://games.example.org" ]
  [ "$(jq -r .token "$GOTG_CONFIG_DIR/api.json")" = tok456 ]
  grep -q 'server = "https://games.example.org";' "$GOTG_CONFIG_DIR/library/flake.nix"
}

@test "no token at all says what to do, and runs nothing" {
  play n64.usa.donkey_kong_64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"GOTG_TOKEN="* ]]
  [[ "$stderr" == *"--claim"* ]]
  ! grep -q "run " "$NIX_CALLS"
}

@test "an http server would send the token in the clear, and is refused" {
  GOTG_TOKEN=tok GOTG_SERVER=http://games.example.org play usa.donkey_kong_64
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"in the clear"* ]]
  [ ! -e "$GOTG_CONFIG_DIR/api.json" ]
}

@test "no game named is the usage, not a library" {
  with_token
  play
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage"* ]]
  [ ! -e "$GOTG_CONFIG_DIR/library" ]
}
