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
if [[ "$1 $2" == "flake update" ]]; then
  : >"$TEST_TMP/catalog-updated"
  exit 0
fi
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

@test "offline, a game already rooted still runs, and says it is not rebuilt" {
  gotg play usa.zelda
  export NIX_OFFLINE=1
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"n64.usa.zelda ran with:"* ]]
  [[ "$stderr" == *"could not rebuild"* ]]
}

@test "offline, a game never built cannot run, and says why" {
  export NIX_OFFLINE=1
  gotg play usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"$GOTG_LIBRARY"* ]]
}

@test "with no library, play is what it always was" {
  unset GOTG_LIBRARY
  fake_env env-n64
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-n64 launched with:"* ]]
  ! grep -q "#n64.usa.zelda" "$NIX_LOG" 2>/dev/null
}

@test "update rebuilds every game that has a root, and only those" {
  gotg play usa.zelda
  : >"$NIX_LOG"
  gotg update
  [ "$status" -eq 0 ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda -o $(GAMES)/n64.usa.zelda" "$NIX_LOG"
  [ "$(grep -c ' build \|^build ' "$NIX_LOG")" = 1 ]
}
