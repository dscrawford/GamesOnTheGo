#!/usr/bin/env bats
# What is out of date here: gotg, the picker, and the games -- answered from
# a cache for the picker, worked out by `gotg update --check`, and done by
# `gotg update self` and `gotg update <platform>/<id>`. See
# src/client/lib/updates.sh and library-play.sh.

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
  lock_gotg_at "aaaaaaaa"
  # What the shim answers; a test exports its own before asking.
  export LIBRARY_LATEST="aaaaaaaa" EVAL_GAMES_JSON='{}'
  export EVAL_APPS_JSON='{"gotg":"/nix/store/app-gotg","ui":"/nix/store/ui-gotg-ui"}'
  updates_nix
}

teardown() {
  stop_saves_service
}

GAMES() { printf '%s/games' "$GOTG_STATE_DIR"; }
APP_ROOT() { printf '%s/app' "$GOTG_STATE_DIR"; }
UI_ROOT() { printf '%s/picker' "$GOTG_STATE_DIR"; }
cache_file() { printf '%s/updates.json' "$GOTG_STATE_DIR"; }
sha() { printf "$1%.0s" {1..64}; }

# A library lock whose gotg input is GitHub's, locked at one revision.
lock_gotg_at() {
  jq -n --arg rev "$1" '{nodes: {gotg: {original: {type: "github", owner: "dscrawford", repo: "GamesOnTheGo"},
                                       locked: {type: "github", rev: $rev}}}}' >"$GOTG_LIBRARY/flake.lock"
}

# A nix that answers every question the client asks, and records each call:
# `flake metadata` with LIBRARY_LATEST as the head; `eval` of the games with
# EVAL_GAMES_JSON and of the apps with EVAL_APPS_JSON; `flake update`, which
# moves the lock's gotg to ffffffff so a restore is observable; `build
# --no-link --print-out-paths`, a store path per installable; `build <store
# path> -o <root>`, a root that is that path; and `build <installable> -o
# <root>`, a game root that says which game it is, as library.bats' does.
# NIX_OFFLINE refuses everything; FAIL_MATCH is a regex over the argv that
# refuses one call.
updates_nix() {
  export NIX_LOG="$TEST_TMP/nix.log" GOTG_NIX="$TEST_TMP/bin/nix" SHIM_BASH
  SHIM_BASH="$(command -v bash)"
  mkdir -p "$TEST_TMP/bin"
  cat >"$GOTG_NIX" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$NIX_LOG"
[[ -z "${NIX_OFFLINE:-}" ]] || exit 1
if [[ "$1" == run ]]; then echo "ran: $*"; exit 0; fi
if [[ -n "${FAIL_MATCH:-}" && "$*" =~ $FAIL_MATCH ]]; then echo "error: refused by the test" >&2; exit 1; fi
case "$1 $2" in
  "flake metadata") printf '{"locked":{"rev":"%s"}}\n' "$LIBRARY_LATEST"; exit 0 ;;
  "flake update")
    lib="${*: -1}"
    if [[ " $* " == *" gotg "* ]]; then
      jq '.nodes.gotg.locked.rev = "ffffffff"' "$lib/flake.lock" >"$lib/flake.lock.new" && mv "$lib/flake.lock.new" "$lib/flake.lock"
    fi
    exit 0 ;;
  "eval --json")
    for arg in "$@"; do
      case "$arg" in
        *"#legacyPackages."*) printf '%s\n' "$EVAL_GAMES_JSON"; exit 0 ;;
        *"#packages."*) printf '%s\n' "$EVAL_APPS_JSON"; exit 0 ;;
      esac
    done
    exit 1 ;;
esac
if [[ "$1" == build && "$*" == *"#catalog"* ]]; then
  [[ -n "${LIBRARY_CATALOG:-}" ]] || exit 1
  prev=""; for arg in "$@"; do [[ "$prev" == "-o" ]] && { rm -f "$arg"; ln -sfn "$LIBRARY_CATALOG" "$arg"; exit 0; }; prev="$arg"; done
  exit 1
fi
if [[ "$1" == build && "$*" == *"--print-out-paths"* ]]; then
  for arg in "$@"; do case "$arg" in *"#"*) echo "$TEST_TMP/store/${arg#*#}-out" ;; esac; done
  exit 0
fi
if [[ "$1" == build && "$*" == *"--no-link"* ]]; then exit 0; fi
out="" prev="" installable=""
for arg in "$@"; do
  [[ "$prev" == "-o" ]] && out="$arg"
  [[ "$arg" == *"#"* ]] && installable="$arg"
  prev="$arg"
done
[[ -n "$out" ]] || exit 1
if [[ "$2" == "$TEST_TMP/store/"* ]]; then
  mkdir -p "$2/bin"; rm -rf "$out"; ln -sfn "$2" "$out"; exit 0
fi
rm -rf "$out"; mkdir -p "$out/bin"
printf '#!%s\necho "%s ran with: $*"\n' "$SHIM_BASH" "${installable#*#}" >"$out/bin/gotg-game"
chmod +x "$out/bin/gotg-game"
SHIM
  sed -i "1s|.*|#!$SHIM_BASH|" "$GOTG_NIX"
  chmod +x "$GOTG_NIX"
}

# A game's root as `gotg update` would leave it: a link under games/ to a
# store path, made by hand so a test can say what it points at.
root_for() {
  local attr="$1" path="$2"
  mkdir -p "$(GAMES)" "$TEST_TMP/store/$path/bin"
  ln -sfn "$TEST_TMP/store/$path" "$(GAMES)/$attr"
}
real_root() { printf '%s/store/%s' "$TEST_TMP" "$1"; }

wants() { export EVAL_GAMES_JSON="$1"; }

# --- the quick question ----------------------------------------------------------

@test "with nothing checked yet, complete updates says pending and nothing else, and asks nix nothing" {
  gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -r .version <<<"$output")" = 1 ]
  [ "$(jq -r .pending <<<"$output")" = true ]
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
  [ "$(jq -r .gotg.available <<<"$output")" = false ]
  [ ! -e "$NIX_LOG" ]
}

@test "a corrupt or wrongly typed cache still answers, as pending, and does not kill the client" {
  local body
  for body in 'garbage' '{"stamp": 5, "stale": "yes", "checked_at": "x"}' '[]'; do
    printf '%s' "$body" >"$(cache_file)"
    gotg complete updates
    [ "$status" -eq 0 ] || { echo "cache: $body" >&2; return 1; }
    [ "$(jq -r .version <<<"$output")" = 1 ]
    [ "$(jq -r .pending <<<"$output")" = true ]
  done
}

@test "a library in the store cannot be updated, and says so" {
  export GOTG_LIBRARY="/nix/store/zzzz-library"
  gotg complete updates
  [ "$(jq -r .writable <<<"$output")" = false ]
  export GOTG_LIBRARY="$TEST_TMP/library"
  gotg complete updates
  [ "$(jq -r .writable <<<"$output")" = true ]
}

# --- the check -------------------------------------------------------------------

@test "a check asks where gotg came from for its head, and a lock behind it is behind" {
  export LIBRARY_LATEST="bbbbbbbb"
  gotg update --check
  [ "$status" -eq 0 ]
  grep -q "^flake metadata --json github:dscrawford/GamesOnTheGo" "$NIX_LOG"
  [ "$(jq -r .gotg.locked <<<"$output")" = aaaaaaaa ]
  [ "$(jq -r .gotg.latest <<<"$output")" = bbbbbbbb ]
  [ "$(jq -r .gotg.behind <<<"$output")" = true ]
  [ "$(jq -r .gotg.available <<<"$output")" = true ]
  [ "$(jq -r .pending <<<"$output")" = false ]
  # And the answer is now the cache's, for the quick question.
  gotg complete updates
  [ "$(jq -r .gotg.behind <<<"$output")" = true ]
}

@test "a lock at the head is not behind, and roots never made are not unbuilt" {
  gotg update --check
  [ "$(jq -r .gotg.behind <<<"$output")" = false ]
  [ "$(jq -r .gotg.unbuilt <<<"$output")" = false ]
  [ "$(jq -r .gotg.available <<<"$output")" = false ]
}

@test "offline, the last head is kept and the answer says it is stale" {
  export LIBRARY_LATEST="bbbbbbbb"
  gotg update --check
  export NIX_OFFLINE=1
  gotg update --check --force
  [ "$status" -eq 0 ]
  [ "$(jq -r .gotg.latest <<<"$output")" = bbbbbbbb ]
  [ "$(jq -r .stale <<<"$output")" = true ]
}

@test "a check within its interval asks nix nothing; --force asks again" {
  gotg update --check
  rm -f "$NIX_LOG"
  gotg update --check
  [ ! -e "$NIX_LOG" ]
  gotg update --check --force
  grep -q "flake metadata" "$NIX_LOG"
}

@test "a library whose gotg is not GitHub's has no head to be behind" {
  jq -n '{nodes: {gotg: {original: {type: "path", path: "/home/me/GOTG"}, locked: {type: "path", rev: "cccccccc"}}}}' \
    >"$GOTG_LIBRARY/flake.lock"
  gotg update --check
  [ "$status" -eq 0 ]
  ! grep -q "flake metadata" "$NIX_LOG"
  [ "$(jq -r .gotg.behind <<<"$output")" = false ]
}

@test "a lock with no gotg node, or one that is not JSON, is checked and is not behind" {
  local lock
  for lock in '{"nodes":{"root":{"inputs":{}}},"version":7}' 'not json'; do
    rm -f "$NIX_LOG" "$(cache_file)"
    printf '%s' "$lock" >"$GOTG_LIBRARY/flake.lock"
    gotg update --check
    [ "$status" -eq 0 ] || { echo "lock: $lock" >&2; return 1; }
    ! grep -q "flake metadata" "$NIX_LOG"
    [ "$(jq -r .gotg.behind <<<"$output")" = false ]
  done
}

@test "an evaluation that answers garbage keeps the last answer, says stale, and leaves no half-written cache" {
  root_for n64.usa.zelda zelda
  wants "$(jq -nc --arg z "$(real_root new-zelda)" '{"n64.usa.zelda": $z}')"
  gotg update --check
  wants 'not json'
  gotg update --check --force
  [ "$status" -eq 0 ]
  [ "$(jq -r .stale <<<"$output")" = true ]
  [ "$(jq -r '.want["n64.usa.zelda"]' "$(cache_file)")" = "$(real_root new-zelda)" ]
  [ ! -e "$(cache_file).tmp" ]
}

# --- games -----------------------------------------------------------------------

@test "a game whose root is not what the library would build has an update; one that is does not" {
  root_for n64.usa.zelda old-zelda
  root_for n64.usa.mario mario
  wants "$(jq -nc --arg z "$(real_root new-zelda)" --arg m "$(real_root mario)" '{"n64.usa.zelda": $z, "n64.usa.mario": $m}')"
  gotg update --check
  [ "$status" -eq 0 ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.zelda"]' ]
  [ "$(jq -c '.games[0].attrs' <<<"$output")" = '["n64.usa.zelda"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["build"]' ]
}

@test "variants of one game are gathered under it, and only the stale ones are named" {
  root_for n64.usa.zelda zelda
  root_for n64.usa.zelda.rando old-rando
  root_for n64.usa.zelda.rando-hd old-hd
  wants "$(jq -nc --arg z "$(real_root zelda)" --arg r "$(real_root new-rando)" --arg h "$(real_root new-hd)" \
    '{"n64.usa.zelda": $z, "n64.usa.zelda.rando": $r, "n64.usa.zelda.rando-hd": $h}')"
  gotg update --check
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.zelda"]' ]
  [ "$(jq -c '.games[0].attrs | sort' <<<"$output")" = '["n64.usa.zelda.rando","n64.usa.zelda.rando-hd"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["build"]' ]
  # The current plain root was stamped current; the stale variants were not.
  [ -e "$(GAMES)/n64.usa.zelda.by" ]
  [ ! -e "$(GAMES)/n64.usa.zelda.rando.by" ]
}

@test "a root the library no longer has (want null) is not an update" {
  root_for n64.usa.zelda.rando old
  wants '{"n64.usa.zelda.rando": null}'
  gotg update --check
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
}

@test "a root that is current is stamped current, and the stamp says nothing once the lock moves" {
  root_for n64.usa.zelda zelda
  wants "$(jq -nc --arg z "$(real_root zelda)" '{"n64.usa.zelda": $z}')"
  gotg update --check
  [ "$(cat "$(GAMES)/n64.usa.zelda.by")" = "$GOTG_LIBRARY $(sha256sum "$GOTG_LIBRARY/flake.lock" | cut -d' ' -f1)" ]
  # The lock moves: the evaluation was for the old one, so nothing is said.
  lock_gotg_at "dddddddd"
  gotg complete updates
  [ "$(jq -r .pending <<<"$output")" = true ]
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
  # A new check says what the new lock wants.
  wants "$(jq -nc --arg z "$(real_root newer-zelda)" '{"n64.usa.zelda": $z}')"
  gotg update --check
  [ "$(jq -r .pending <<<"$output")" = false ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.zelda"]' ]
}

@test "the picker's and the client's roots are unbuilt when they are not what the lock would build" {
  mkdir -p "$TEST_TMP/store/app-gotg" "$TEST_TMP/store/old-ui" "$TEST_TMP/store/new-ui" "$GOTG_STATE_DIR"
  ln -sfn "$TEST_TMP/store/app-gotg" "$(APP_ROOT)"
  ln -sfn "$TEST_TMP/store/old-ui" "$(UI_ROOT)"
  export EVAL_APPS_JSON="$(jq -nc --arg a "$TEST_TMP/store/app-gotg" --arg u "$TEST_TMP/store/new-ui" '{gotg: $a, ui: $u}')"
  gotg update --check
  [ "$(jq -r .gotg.unbuilt <<<"$output")" = true ]
  [ "$(jq -r .gotg.available <<<"$output")" = true ]
  [ "$(jq -r .gotg.picker <<<"$output")" = "$TEST_TMP/store/old-ui" ]
  [ "$(jq -r .gotg.picker_current <<<"$output")" = false ]
  ln -sfn "$TEST_TMP/store/new-ui" "$(UI_ROOT)"
  gotg complete updates
  [ "$(jq -r .gotg.unbuilt <<<"$output")" = false ]
  [ "$(jq -r .gotg.picker_current <<<"$output")" = true ]
}

@test "an installed bundle missing a release the catalog attached has an update" {
  local dir="$SERVICE_LIBRARY_DIR/switch"
  mkdir -p "$dir/upd"
  printf 'base' >"$dir/world.link.nsp"
  printf 'update' >"$dir/upd/u.rar"
  add_member_game switch world.link "Link" single_file "$(jq -n --arg d "$dir" --arg a "$(sha a)" --arg b "$(sha b)" \
    '[{name: "world.link.nsp", path: ($d + "/world.link.nsp"), size_bytes: 4, mtime: 1, sha256: $a},
      {name: "extras/update_1.1.0/u.rar", path: ($d + "/upd/u.rar"), size_bytes: 6, mtime: 1, sha256: $b}]')"
  # The service's catalog, not the library's pinned copy: with a library set,
  # refresh builds the pin through the nix shim, which knows no catalog.
  GOTG_LIBRARY= gotg refresh
  mkdir -p "$GOTG_GAMES_DIR/switch/world.link/extras"
  printf 'base' >"$GOTG_GAMES_DIR/switch/world.link/world.link.nsp"
  gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["switch/world.link"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["extras"]' ]
  # The release arrives beside it: nothing to say.
  touch "$GOTG_GAMES_DIR/switch/world.link/extras/update_1.1.0-u.rar"
  gotg complete updates
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
}

@test "an n64 game lacking its mod release has an extras update" {
  local dir="$SERVICE_LIBRARY_DIR/n64"
  mkdir -p "$dir/mods"
  printf 'rom' >"$dir/usa.paper_mario.z64"
  printf 'pack' >"$dir/mods/paperboat-hd.o2r"
  add_member_game n64 usa.paper_mario "Paper Mario" single_file "$(jq -n --arg d "$dir" --arg a "$(sha a)" --arg b "$(sha b)" \
    '[{name: "usa.paper_mario.z64", path: ($d + "/usa.paper_mario.z64"), size_bytes: 3, mtime: 1, sha256: $a},
      {name: "extras/mod_refolded/paperboat-hd.o2r", path: ($d + "/mods/paperboat-hd.o2r"), size_bytes: 4, mtime: 1, sha256: $b}]')"
  GOTG_LIBRARY= gotg refresh
  mkdir -p "$GOTG_GAMES_DIR/n64/usa.paper_mario/extras"
  printf 'rom' >"$GOTG_GAMES_DIR/n64/usa.paper_mario/usa.paper_mario.z64"
  gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.paper_mario"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["extras"]' ]
}

@test "a game still installed as a plain file is behind the mod its row carries" {
  local dir="$SERVICE_LIBRARY_DIR/n64"
  mkdir -p "$dir/mods"
  printf 'rom' >"$dir/usa.paper_mario.z64"
  printf 'pack' >"$dir/mods/paperboat-hd.o2r"
  add_member_game n64 usa.paper_mario "Paper Mario" single_file "$(jq -n --arg d "$dir" --arg a "$(sha a)" --arg b "$(sha b)" \
    '[{name: "usa.paper_mario.z64", path: ($d + "/usa.paper_mario.z64"), size_bytes: 3, mtime: 1, sha256: $a},
      {name: "extras/mod_refolded/paperboat-hd.o2r", path: ($d + "/mods/paperboat-hd.o2r"), size_bytes: 4, mtime: 1, sha256: $b}]')"
  GOTG_LIBRARY= gotg refresh
  mkdir -p "$GOTG_GAMES_DIR/n64"
  printf 'rom' >"$GOTG_GAMES_DIR/n64/usa.paper_mario.z64"
  gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.paper_mario"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["extras"]' ]
}

@test "a check refreshes the library's catalog first, so a mod attached since shows" {
  local dir="$SERVICE_LIBRARY_DIR/n64"
  mkdir -p "$dir/mods" "$GOTG_GAMES_DIR/n64"
  printf 'rom' >"$dir/usa.paper_mario.z64"
  printf 'rom' >"$GOTG_GAMES_DIR/n64/usa.paper_mario.z64"
  printf 'pack' >"$dir/mods/paperboat-hd.o2r"
  add_member_game n64 usa.paper_mario "Paper Mario" single_file "$(jq -n --arg d "$dir" --arg a "$(sha a)" --arg b "$(sha b)" \
    '[{name: "usa.paper_mario.z64", path: ($d + "/usa.paper_mario.z64"), size_bytes: 3, mtime: 1, sha256: $a},
      {name: "extras/mod_refolded/paperboat-hd.o2r", path: ($d + "/mods/paperboat-hd.o2r"), size_bytes: 4, mtime: 1, sha256: $b}]')"
  curl -sf -H "Authorization: Bearer $(jq -r .token "$GOTG_CONFIG_DIR/api.json")" "$GOTG_SERVICE_URL/catalog" >"$TEST_TMP/catalog.json"
  export LIBRARY_CATALOG="$TEST_TMP/catalog.json"
  gotg update --check --force
  [ "$status" -eq 0 ]
  grep -qx "flake update catalog --flake $GOTG_LIBRARY" "$NIX_LOG"
  gotg complete updates
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.paper_mario"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["extras"]' ]
}

@test "a check whose catalog cannot be read keeps the one it has" {
  export FAIL_MATCH='#catalog'
  gotg update --check --force
  [ "$status" -eq 0 ]
  jq -e '.games | length > 0' "$GOTG_CACHE_FILE" >/dev/null
  gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
}

# --- one game brought up to date -------------------------------------------------

@test "update <platform>/<id> rebuilds that game's roots, its variants with it, and no other" {
  root_for n64.usa.zelda zelda
  root_for n64.usa.zelda.rando zelda-rando
  root_for n64.usa.mario mario
  gotg update n64/usa.zelda
  [ "$status" -eq 0 ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda -o $(GAMES)/n64.usa.zelda" "$NIX_LOG"
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda.rando -o $(GAMES)/n64.usa.zelda.rando" "$NIX_LOG"
  ! grep -qF "#n64.usa.mario" "$NIX_LOG"
  [[ "$stderr" == *"n64.usa.zelda: up to date"* ]]
}

@test "update of a game with no build here says so and does not fail" {
  gotg update n64/usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no build here yet"* ]]
}

# --- gotg itself -----------------------------------------------------------------

prep_self_update() {
  mkdir -p "$GOTG_STATE_DIR" "$TEST_TMP/store/was-app" "$TEST_TMP/store/was-ui"
  ln -sfn "$TEST_TMP/store/was-app" "$(APP_ROOT)"
  ln -sfn "$TEST_TMP/store/was-ui" "$(UI_ROOT)"
  lock_gotg_at "aaaaaaaa"
  cp "$GOTG_LIBRARY/flake.lock" "$TEST_TMP/lock-before"
  rm -f "$GOTG_LIBRARY/flake.lock.before-update"
}

assert_untouched() {
  cmp -s "$GOTG_LIBRARY/flake.lock" "$TEST_TMP/lock-before" || { echo "lock changed: $1" >&2; return 1; }
  [ ! -e "$GOTG_LIBRARY/flake.lock.before-update" ] || { echo "backup left: $1" >&2; return 1; }
  [ "$(readlink -f "$(APP_ROOT)")" = "$TEST_TMP/store/was-app" ] || { echo "app root moved: $1" >&2; return 1; }
  [ "$(readlink -f "$(UI_ROOT)")" = "$TEST_TMP/store/was-ui" ] || { echo "ui root moved: $1" >&2; return 1; }
}

@test "update self moves the pin, builds both with nothing pointed at them, then swaps the roots" {
  prep_self_update
  gotg update self
  [ "$status" -eq 0 ]
  grep -q "^flake update gotg catalog --flake $GOTG_LIBRARY\$" "$NIX_LOG"
  grep -q "^build $GOTG_LIBRARY#gotg $GOTG_LIBRARY#gotg-ui --no-link" "$NIX_LOG"
  grep -q "^build $TEST_TMP/store/gotg-out -o $(APP_ROOT)\$" "$NIX_LOG"
  grep -q "^build $TEST_TMP/store/gotg-ui-out -o $(UI_ROOT)\$" "$NIX_LOG"
  [ "$(jq -r .nodes.gotg.locked.rev "$GOTG_LIBRARY/flake.lock")" = ffffffff ]
  [ "$(readlink -f "$(APP_ROOT)")" = "$TEST_TMP/store/gotg-out" ]
  [ "$(readlink -f "$(UI_ROOT)")" = "$TEST_TMP/store/gotg-ui-out" ]
  [ ! -e "$GOTG_LIBRARY/flake.lock.before-update" ]
  # No swap before every build has finished.
  [ "$(grep -n -- '--print-out-paths' "$NIX_LOG" | tail -1 | cut -d: -f1)" -lt \
    "$(grep -n -- ' -o ' "$NIX_LOG" | head -1 | cut -d: -f1)" ]
  # The last line, for the picker: where the new picker is.
  [ "$(tail -n1 <<<"$output")" = "$(printf 'picker\t%s' "$TEST_TMP/store/gotg-ui-out")" ]
}

@test "a launch with --refresh moves the pin, then runs the game again from the library" {
  prep_self_update
  mkdir -p "$TEST_TMP/store/env/bin"
  printf '#!/bin/sh\n' >"$TEST_TMP/store/env/bin/gotg-play"
  chmod +x "$TEST_TMP/store/env/bin/gotg-play"
  local catalog
  catalog="$(curl -gfsS -H "Authorization: Bearer test-token" "$GOTG_SERVICE_URL/catalog")"
  jq -n --argjson c "$catalog" --arg env "$TEST_TMP/store/env" --arg server "$GOTG_SERVICE_URL" \
    '{version: 1, server: $server, files_urls: [($c.files_url // empty)], attr: "env-n64", env: $env,
      variant: "", output: "n64.usa.zelda", game: ($c.games[] | select(.id == "usa.zelda"))}' >"$TEST_TMP/spec.json"
  gotg launch --spec "$TEST_TMP/spec.json" --refresh --version 1.1 --fast
  [ "$status" -eq 0 ]
  [ "$(jq -r .nodes.gotg.locked.rev "$GOTG_LIBRARY/flake.lock")" = ffffffff ]
  grep -q "^flake update gotg catalog --flake $GOTG_LIBRARY\$" "$NIX_LOG"
  [ "$(tail -n1 "$NIX_LOG")" = "run $GOTG_LIBRARY#n64.usa.zelda -- --version 1.1 --fast" ]
  [[ "$output" == *"ran: run $GOTG_LIBRARY#n64.usa.zelda"* ]]
}

@test "update self that fails at any step leaves the lock and both roots as they were" {
  local p
  for p in '^flake update' 'gotg-ui --no-link$' '#gotg --no-link --print-out-paths$' \
    '#gotg-ui --no-link --print-out-paths$' "^build $TEST_TMP/store/gotg-out -o" "^build $TEST_TMP/store/gotg-ui-out -o"; do
    prep_self_update
    export FAIL_MATCH="$p"
    gotg update self
    [ "$status" -ne 0 ] || { echo "succeeded despite refusing: $p" >&2; return 1; }
    [[ "$stderr" == *"nothing was changed"* ]] || { echo "no restore message: $p" >&2; return 1; }
    assert_untouched "$p"
  done
}

@test "update self refuses a library it cannot write" {
  export GOTG_LIBRARY="/nix/store/zzzz-library"
  gotg update self
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"cannot be moved from here"* ]]
  [ ! -e "$NIX_LOG" ]
}

@test "update self with no lock file says so rather than dying on cp" {
  rm -f "$GOTG_LIBRARY/flake.lock"
  gotg update self
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no flake.lock"* ]]
  [ ! -e "$GOTG_LIBRARY/flake.lock.before-update" ]
}

@test "update with an argument it does not know says its usage" {
  gotg update --frobnicate
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg update"* ]]
}
