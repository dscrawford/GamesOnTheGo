#!/usr/bin/env bats
# `gotg play` through a library flake (docs/nix-games.md): with one
# configured, a game is its Nix output -- built into a GC root of its own and
# run -- so the picker and Steam, which both run `gotg play`, play games the
# Nix way without knowing it. The catalog is still the client's cache, which
# the picker searches; the library's pin follows it when a game is newer.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  export GOTG_LIBRARY="$TEST_TMP/library"
  mkdir -p "$GOTG_LIBRARY"
  : >"$GOTG_LIBRARY/flake.nix"
  library_nix
}

teardown() {
  stop_saves_service
}

GAMES() { printf '%s/games' "$GOTG_STATE_DIR"; }

# A nix that builds a game's app into the -o root -- an app that says which
# game it is -- and records every call. LIBRARY_LACKS names an attribute the
# pinned catalog does not have until `flake update catalog`; NIX_OFFLINE fails
# every build.
library_nix() {
  export NIX_LOG="$TEST_TMP/nix.log" GOTG_NIX="$TEST_TMP/bin/nix" SHIM_BASH
  SHIM_BASH="$(command -v bash)"
  mkdir -p "$TEST_TMP/bin"
  cat >"$GOTG_NIX" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$NIX_LOG"
printf '%s\n' "${NIX_CONFIG:-}" >>"$TEST_TMP/nix-config.log"
if [[ "$1 $2" == "flake update" ]]; then
  : >"$TEST_TMP/catalog-updated"
  exit 0
fi
[[ "$1" != registry ]] || exit 0
[[ -z "${NIX_OFFLINE:-}" ]] || exit 1
out="" prev="" installable=""
for arg in "$@"; do
  [[ "$prev" == "-o" ]] && out="$arg"
  [[ "$arg" == *"#"* ]] && installable="$arg"
  prev="$arg"
done
[[ -n "$out" ]] || exit 1
if [[ -n "${LIBRARY_LACKS:-}" && "$installable" == *"#$LIBRARY_LACKS" && ! -e "$TEST_TMP/catalog-updated" ]]; then
  echo "error: flake does not provide attribute '$LIBRARY_LACKS'" >&2
  exit 1
fi
if [[ -n "${LIBRARY_GONE:-}" && "$installable" == *"#$LIBRARY_GONE" ]]; then
  echo "error: flake does not provide attribute '$LIBRARY_GONE'" >&2
  exit 1
fi
if [[ -n "${LIBRARY_STALE:-}" && ! -e "$TEST_TMP/catalog-updated" ]]; then
  echo "error: mismatch in field 'narHash' of input '{\"type\":\"file\",\"url\":\"https://gotg.example/catalog\"}'" >&2
  exit 1
fi
rm -rf "$out"; mkdir -p "$out/bin"
printf '#!%s\necho "%s ran with: $*"\n' "$SHIM_BASH" "${installable#*#}" >"$out/bin/gotg-game"
chmod +x "$out/bin/gotg-game"
SHIM
  sed -i "1s|.*|#!$SHIM_BASH|" "$GOTG_NIX"
  chmod +x "$GOTG_NIX"
}

@test "a game is its library's output, built into a root of its own and run" {
  gotg play usa.zelda --fullscreen
  [ "$status" -eq 0 ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda -o $(GAMES)/n64.usa.zelda" "$NIX_LOG"
  [[ "$output" == *"n64.usa.zelda ran with: --fullscreen"* ]]
  [ -x "$(GAMES)/n64.usa.zelda/bin/gotg-game" ]
}

# The catalog is a private flake input, fetched with the token login keeps
# in a netrc. A nix.conf that is not the user's to write (Home Manager's, in
# the store) never named it, and a build died on the catalog's 401: so every
# nix this runs is handed the netrc, unless Nix has one of its own.
@test "a build is handed the netrc login keeps" {
  export GOTG_SYSTEM_NETRC="$TEST_TMP/no-system-netrc"
  printf 'machine 127.0.0.1 login gotg password tok\n' >"$GOTG_CONFIG_DIR/netrc"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  grep -qx "netrc-file = $GOTG_CONFIG_DIR/netrc" "$TEST_TMP/nix-config.log"
}

@test "a netrc Nix already reads is not hidden behind ours" {
  export GOTG_SYSTEM_NETRC="$TEST_TMP/system-netrc"
  : >"$GOTG_SYSTEM_NETRC"
  printf 'machine 127.0.0.1 login gotg password tok\n' >"$GOTG_CONFIG_DIR/netrc"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  ! grep -q "netrc-file" "$TEST_TMP/nix-config.log"
}

@test "no netrc, nothing handed" {
  export GOTG_SYSTEM_NETRC="$TEST_TMP/no-system-netrc"
  rm -f "$GOTG_CONFIG_DIR/netrc"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  ! grep -q "netrc-file" "$TEST_TMP/nix-config.log"
}

@test "a variant is an attribute of its game" {
  printf '{}' >"$GOTG_ENV_DIR/n64.nix"
  mkdir -p "$GOTG_ENV_DIR/games/n64"
  : >"$GOTG_ENV_DIR/games/n64/usa.zelda.rando.nix"
  gotg play usa.zelda rando
  [ "$status" -eq 0 ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda.rando -o $(GAMES)/n64.usa.zelda.rando" "$NIX_LOG"
}

@test "a game the library's pin does not have yet updates the catalog, once, and runs" {
  export LIBRARY_LACKS=n64.usa.zelda TEST_TMP
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  grep -qx "flake update catalog --flake $GOTG_LIBRARY" "$NIX_LOG"
  [[ "$output" == *"n64.usa.zelda ran with:"* ]]
}

# The lock pins the catalog by hash. Once that file has left the store, Nix
# fetches the url again, the server has moved on, and the hash is not the
# lock's: the same answer as a game the pin lacks -- move the pin, once.
@test "a catalog the server has moved past updates the pin, once, and runs" {
  export LIBRARY_STALE=1 TEST_TMP
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ "$(grep -cx "flake update catalog --flake $GOTG_LIBRARY" "$NIX_LOG")" = 1 ]
  [[ "$output" == *"n64.usa.zelda ran with:"* ]]
}

@test "a game built from the library as it is launches from its root, with no build" {
  gotg play usa.zelda
  : >"$NIX_LOG"
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"n64.usa.zelda ran with:"* ]]
  ! grep -q build "$NIX_LOG"
}

@test "a library that moved rebuilds a game; offline, the root it has still runs" {
  gotg play usa.zelda
  printf '{"nodes": {}}' >"$GOTG_LIBRARY/flake.lock"
  export NIX_OFFLINE=1
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"n64.usa.zelda ran with:"* ]]
  [[ "$stderr" == *"could not rebuild"* ]]
  unset NIX_OFFLINE
  : >"$NIX_LOG"
  gotg play usa.zelda
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda" "$NIX_LOG"
}

@test "offline, a game never built cannot run, and says why" {
  export NIX_OFFLINE=1
  gotg play usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"$GOTG_LIBRARY"* ]]
}

@test "with no library, play says how to make one" {
  unset GOTG_LIBRARY
  fake_env env-n64
  gotg play usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"nix flake init -t"*"#library"* ]]
  [[ "$stderr" == *"gotg library"* ]]
  [[ "$output" != *"launched with"* ]]
}

@test "gotg library sets the library, as an absolute directory, and says it" {
  unset GOTG_LIBRARY
  mkdir -p "$TEST_TMP/mylib"
  : >"$TEST_TMP/mylib/flake.nix"
  cd "$TEST_TMP"
  gotg library mylib
  [ "$status" -eq 0 ]
  gotg library
  [ "$output" = "$TEST_TMP/mylib" ]
  # And names it gotg for Nix: the short form of everything.
  grep -qx "registry add gotg $TEST_TMP/mylib" "$NIX_LOG"
}

@test "gotg library refuses a directory with no flake in it" {
  unset GOTG_LIBRARY
  mkdir -p "$TEST_TMP/empty"
  gotg library "$TEST_TMP/empty"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no flake.nix"* ]]
}

@test "update rebuilds every game that has a root, and only those" {
  gotg play usa.zelda
  : >"$NIX_LOG"
  gotg update
  [ "$status" -eq 0 ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda -o $(GAMES)/n64.usa.zelda" "$NIX_LOG"
  # One game build; the client and the picker Steam starts are rebuilt too.
  [ "$(grep -c '#n64\.' "$NIX_LOG")" = 1 ]
  grep -qF "build $GOTG_LIBRARY#gotg -o $GOTG_STATE_DIR/app" "$NIX_LOG"
  grep -qF "build $GOTG_LIBRARY#gotg-ui -o $GOTG_STATE_DIR/picker" "$NIX_LOG"
}

# A variant the library dropped -- Paper Mario's recut, when PaperBoat took
# its place -- left a root that failed every update after it, for good. Gone
# is not broken: it is said, with how to let it go, and the update succeeds.
@test "update says a game the library no longer has is gone, and does not fail on it" {
  printf '{}' >"$GOTG_ENV_DIR/n64.nix"
  mkdir -p "$GOTG_ENV_DIR/games/n64"
  : >"$GOTG_ENV_DIR/games/n64/usa.zelda.rando.nix"
  gotg play usa.zelda
  gotg play usa.zelda rando
  : >"$NIX_LOG"
  export LIBRARY_GONE=n64.usa.zelda.rando TEST_TMP
  gotg update
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"n64.usa.zelda.rando is no longer in the library"* ]]
  [[ "$stderr" == *"rm $(GAMES)/n64.usa.zelda.rando"* ]]
  [ -e "$(GAMES)/n64.usa.zelda.rando" ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda -o $(GAMES)/n64.usa.zelda" "$NIX_LOG"
}

@test "what is built here is what the games' specs name, and what roots/ still holds" {
  # saves --all, controllers --all and the GL sync walk this: with games built
  # from a library there is nothing under roots/, and walking only that was
  # every one of them finding nothing.
  load_client_libs
  local env="$TEST_TMP/store/aaaa-env-n64-usa_zelda"
  mkdir -p "$env" "$GOTG_STATE_DIR/games/n64.usa.zelda/share/gotg" \
    "$GOTG_STATE_DIR/games/n64.usa.gone/share/gotg" "$GOTG_STATE_DIR/roots/env-snes"
  jq -n --arg e "$env" '{version: 1, attr: "env-n64-usa_zelda", env: $e}' \
    >"$GOTG_STATE_DIR/games/n64.usa.zelda/share/gotg/spec.json"
  # A spec whose environment was collected is not something built here.
  jq -n '{version: 1, attr: "env-n64-usa_gone", env: "/nonexistent"}' \
    >"$GOTG_STATE_DIR/games/n64.usa.gone/share/gotg/spec.json"
  : >"$GOTG_STATE_DIR/roots/env-snes.by"
  run env_built_attrs
  [ "$status" -eq 0 ]
  [ "$output" = $'env-n64-usa_zelda\nenv-snes' ]
}


@test "a library's own apps name it as the default, and a configured one still wins" {
  # `nix run <library>#ui` runs a picker that knows where it came from; a
  # machine that has pointed gotg at a writable copy keeps using that one,
  # since a pin can only be moved there.
  unset GOTG_LIBRARY
  GOTG_LIBRARY_DEFAULT="/nix/store/aaaa-library" gotg library
  [ "$status" -eq 0 ]
  [ "$output" = "/nix/store/aaaa-library" ]
  mkdir -p "$TEST_TMP/mylib"
  : >"$TEST_TMP/mylib/flake.nix"
  gotg library "$TEST_TMP/mylib"
  GOTG_LIBRARY_DEFAULT="/nix/store/aaaa-library" gotg library
  [ "$output" = "$TEST_TMP/mylib" ]
}
