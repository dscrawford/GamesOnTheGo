#!/usr/bin/env bats
# What is out of date here: gotg, the picker, and the games -- answered from
# a cache for the picker, and worked out by `gotg update --check`. See
# src/client/lib/updates.sh.

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
  updates_nix
}

teardown() {
  stop_saves_service
}

GAMES() { printf '%s/games' "$GOTG_STATE_DIR"; }

# A library lock whose gotg input is GitHub's, locked at one revision.
lock_gotg_at() {
  jq -n --arg rev "$1" '{nodes: {gotg: {original: {type: "github", owner: "dscrawford", repo: "GamesOnTheGo"},
                                       locked: {type: "github", rev: $rev}}}}' >"$GOTG_LIBRARY/flake.lock"
}

# A nix that answers the check's three questions and records every call:
# `flake metadata` with LIBRARY_LATEST as the head (offline: refused), `eval`
# of the games with EVAL_GAMES_JSON and of the apps with EVAL_APPS_JSON, and
# `build` with a root that says which game it is, as library.bats' does.
updates_nix() {
  export NIX_LOG="$TEST_TMP/nix.log" GOTG_NIX="$TEST_TMP/bin/nix" SHIM_BASH
  export LIBRARY_LATEST="${LIBRARY_LATEST:-aaaaaaaa}"
  export EVAL_GAMES_JSON="${EVAL_GAMES_JSON:-{\}}"
  export EVAL_APPS_JSON="${EVAL_APPS_JSON:-{\"gotg\":\"/nix/store/app-gotg\",\"ui\":\"/nix/store/ui-gotg-ui\"\}}"
  SHIM_BASH="$(command -v bash)"
  mkdir -p "$TEST_TMP/bin"
  cat >"$GOTG_NIX" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$NIX_LOG"
[[ -z "${NIX_OFFLINE:-}" ]] || exit 1
case "$1 $2" in
  "flake metadata") printf '{"locked":{"rev":"%s"}}\n' "$LIBRARY_LATEST"; exit 0 ;;
  "eval --json")
    for arg in "$@"; do
      case "$arg" in
        *"#legacyPackages."*) printf '%s\n' "$EVAL_GAMES_JSON"; exit 0 ;;
        *"#packages."*) printf '%s\n' "$EVAL_APPS_JSON"; exit 0 ;;
      esac
    done
    exit 1 ;;
esac
out="" prev="" installable=""
for arg in "$@"; do
  [[ "$prev" == "-o" ]] && out="$arg"
  [[ "$arg" == *"#"* ]] && installable="$arg"
  prev="$arg"
done
[[ -n "$out" ]] || exit 1
rm -rf "$out"; mkdir -p "$out/bin"
printf '#!%s\necho "%s ran with: $*"\n' "$SHIM_BASH" "${installable#*#}" >"$out/bin/gotg-game"
chmod +x "$out/bin/gotg-game"
SHIM
  sed -i "1s|.*|#!$SHIM_BASH|" "$GOTG_NIX"
  chmod +x "$GOTG_NIX"
}

# A game's root as `gotg update` would leave it: a directory under games/,
# stamped with the library as it is -- made by hand, so a test can say what
# it points at. $2 is the store path it stands for.
root_for() {
  local attr="$1" path="$2"
  mkdir -p "$TEST_TMP/store/$path/bin"
  ln -sfn "$TEST_TMP/store/$path" "$(GAMES)/$attr"
  touch "$(GAMES)/$attr/bin/gotg-game" 2>/dev/null || true
}
real_root() { printf '%s/store/%s' "$TEST_TMP" "$1"; }

@test "with nothing checked yet, complete updates says pending and nothing else, and asks nix nothing" {
  run gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -r .version <<<"$output")" = 1 ]
  [ "$(jq -r .pending <<<"$output")" = true ]
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
  [ "$(jq -r .gotg.available <<<"$output")" = false ]
  [ ! -e "$NIX_LOG" ]
}

@test "a check asks where gotg came from for its head, and a lock behind it is behind" {
  LIBRARY_LATEST="bbbbbbbb" updates_nix
  run gotg update --check
  [ "$status" -eq 0 ]
  grep -q "^flake metadata --json github:dscrawford/GamesOnTheGo" "$NIX_LOG"
  [ "$(jq -r .gotg.locked <<<"$output")" = aaaaaaaa ]
  [ "$(jq -r .gotg.latest <<<"$output")" = bbbbbbbb ]
  [ "$(jq -r .gotg.behind <<<"$output")" = true ]
  [ "$(jq -r .gotg.available <<<"$output")" = true ]
  [ "$(jq -r .pending <<<"$output")" = false ]
  # And the answer is now the cache's, for the quick question.
  run gotg complete updates
  [ "$(jq -r .gotg.behind <<<"$output")" = true ]
}

@test "a lock at the head is not behind" {
  run gotg update --check
  [ "$(jq -r .gotg.behind <<<"$output")" = false ]
  [ "$(jq -r .gotg.available <<<"$output")" = false ]
}

@test "offline, the last head is kept and the answer says it is stale" {
  LIBRARY_LATEST="bbbbbbbb" updates_nix
  gotg update --check
  export NIX_OFFLINE=1
  run gotg update --check --force
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
  run gotg update --check
  [ "$status" -eq 0 ]
  ! grep -q "flake metadata" "$NIX_LOG"
  [ "$(jq -r .gotg.behind <<<"$output")" = false ]
}

@test "a game whose root is not what the library would build has an update; one that is does not" {
  mkdir -p "$(GAMES)"
  root_for n64.usa.zelda old-zelda
  root_for n64.usa.mario mario
  EVAL_GAMES_JSON="$(jq -nc --arg z "$(real_root new-zelda)" --arg m "$(real_root mario)" '{"n64.usa.zelda": $z, "n64.usa.mario": $m}')" updates_nix
  run gotg update --check
  [ "$status" -eq 0 ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.zelda"]' ]
  [ "$(jq -c '.games[0].attrs' <<<"$output")" = '["n64.usa.zelda"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["build"]' ]
}

@test "a root that is current is stamped current, and the stamp says nothing once the lock moves" {
  mkdir -p "$(GAMES)"
  root_for n64.usa.zelda zelda
  EVAL_GAMES_JSON="$(jq -nc --arg z "$(real_root zelda)" '{"n64.usa.zelda": $z}')" updates_nix
  gotg update --check
  [ "$(cat "$(GAMES)/n64.usa.zelda.by")" = "$GOTG_LIBRARY $(sha256sum "$GOTG_LIBRARY/flake.lock" | cut -d' ' -f1)" ]
  # The lock moves: the evaluation was for the old one, so nothing is said.
  lock_gotg_at "dddddddd"
  run gotg complete updates
  [ "$(jq -r .pending <<<"$output")" = true ]
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
  # A new check says what the new lock wants.
  EVAL_GAMES_JSON="$(jq -nc --arg z "$(real_root newer-zelda)" '{"n64.usa.zelda": $z}')" updates_nix
  run gotg update --check
  [ "$(jq -r .pending <<<"$output")" = false ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["n64/usa.zelda"]' ]
}

@test "the picker's and the client's roots are unbuilt when they are not what the lock would build" {
  mkdir -p "$TEST_TMP/store/app-gotg" "$TEST_TMP/store/old-ui"
  ln -sfn "$TEST_TMP/store/app-gotg" "$GOTG_APP_ROOT"
  ln -sfn "$TEST_TMP/store/old-ui" "$GOTG_UI_ROOT"
  EVAL_APPS_JSON="$(jq -nc --arg a "$TEST_TMP/store/app-gotg" --arg u "$TEST_TMP/store/new-ui" '{gotg: $a, ui: $u}')" updates_nix
  run gotg update --check
  [ "$(jq -r .gotg.unbuilt <<<"$output")" = true ]
  [ "$(jq -r .gotg.available <<<"$output")" = true ]
  [ "$(jq -r .gotg.picker <<<"$output")" = "$TEST_TMP/store/old-ui" ]
  ln -sfn "$TEST_TMP/store/new-ui" "$GOTG_UI_ROOT"
  mkdir -p "$TEST_TMP/store/new-ui"
  run gotg complete updates
  [ "$(jq -r .gotg.unbuilt <<<"$output")" = false ]
}

@test "an installed bundle missing a release the catalog attached has an update" {
  local dir="$SERVICE_LIBRARY_DIR/switch"
  mkdir -p "$dir/upd"
  printf 'base' >"$dir/world.link.nsp"
  printf 'update' >"$dir/upd/u.rar"
  add_member_game switch world.link "Link" single_file "$(jq -n --arg d "$dir" \
    '[{name: "world.link.nsp", path: ($d + "/world.link.nsp"), size_bytes: 4, mtime: 1, sha256: "a"},
      {name: "extras/update_1.1.0/u.rar", path: ($d + "/upd/u.rar"), size_bytes: 6, mtime: 1, sha256: "b"}]')"
  gotg refresh
  mkdir -p "$GOTG_GAMES_DIR/switch/world.link/extras"
  printf 'base' >"$GOTG_GAMES_DIR/switch/world.link/world.link.nsp"
  run gotg complete updates
  [ "$status" -eq 0 ]
  [ "$(jq -c '.games | map(.key)' <<<"$output")" = '["switch/world.link"]' ]
  [ "$(jq -c '.games[0].reasons' <<<"$output")" = '["extras"]' ]
  # The release arrives beside it: nothing to say.
  touch "$GOTG_GAMES_DIR/switch/world.link/extras/update_1.1.0-u.rar"
  run gotg complete updates
  [ "$(jq -r '.games | length' <<<"$output")" = 0 ]
}

@test "a library in the store cannot be updated, and says so" {
  export GOTG_LIBRARY="/nix/store/zzzz-library"
  run gotg complete updates
  [ "$(jq -r .writable <<<"$output")" = false ]
  export GOTG_LIBRARY="$TEST_TMP/library"
  run gotg complete updates
  [ "$(jq -r .writable <<<"$output")" = true ]
}

@test "update <platform>/<id> rebuilds that game's roots, its variants with it, and no other" {
  mkdir -p "$(GAMES)"
  root_for n64.usa.zelda zelda
  root_for n64.usa.zelda.rando zelda-rando
  root_for n64.usa.mario mario
  run gotg update n64/usa.zelda
  [ "$status" -eq 0 ]
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda -o $(GAMES)/n64.usa.zelda" "$NIX_LOG"
  grep -qF "build $GOTG_LIBRARY#n64.usa.zelda.rando -o $(GAMES)/n64.usa.zelda.rando" "$NIX_LOG"
  ! grep -qF "#n64.usa.mario" "$NIX_LOG"
  [[ "$stderr" == *"n64.usa.zelda: up to date"* ]]
}

@test "update of a game with no build here says so and does not fail" {
  run gotg update n64/usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no build here yet"* ]]
}

# For `update self`: the shim answers `build ... --no-link --print-out-paths`
# with a store path per installable, and `build <store path> -o <root>` with
# a root that is that path's directory.
self_update_nix() {
  updates_nix
  python3 - "$GOTG_NIX" <<'EOF'
import sys
path = sys.argv[1]
text = open(path).read()
marker = 'out="" prev="" installable=""\n'
extra = r'''
if [[ "$*" == *"--print-out-paths"* ]]; then
  for arg in "$@"; do
    case "$arg" in *"#"*) echo "$TEST_TMP/store/$(printf '%s' "${arg#*#}")-out" ;; esac
  done
  exit 0
fi
if [[ "$1" == build && "$2" == "$TEST_TMP/store/"* ]]; then
  out="" prev=""
  for arg in "$@"; do [[ "$prev" == "-o" ]] && out="$arg"; prev="$arg"; done
  mkdir -p "$2/bin"; rm -rf "$out"; ln -sfn "$2" "$out"; exit 0
fi
if [[ -n "${BUILD_FAILS:-}" && "$1" == build ]]; then echo "error: build failed" >&2; exit 1; fi
'''
open(path, "w").write(text.replace(marker, extra + marker, 1))
EOF
}

@test "update self moves the pin, builds both with nothing pointed at them, then swaps the roots" {
  self_update_nix
  run gotg update self
  [ "$status" -eq 0 ]
  grep -q "^flake update gotg catalog --flake $GOTG_LIBRARY\$" "$NIX_LOG"
  grep -q "^build $GOTG_LIBRARY#gotg $GOTG_LIBRARY#gotg-ui --no-link" "$NIX_LOG"
  grep -q "^build $TEST_TMP/store/gotg-out -o $GOTG_APP_ROOT\$" "$NIX_LOG"
  grep -q "^build $TEST_TMP/store/gotg-ui-out -o $GOTG_UI_ROOT\$" "$NIX_LOG"
  [ "$(readlink -f "$GOTG_UI_ROOT")" = "$TEST_TMP/store/gotg-ui-out" ]
  [ ! -e "$GOTG_LIBRARY/flake.lock.before-update" ]
  # The last line, for the picker: where the new picker is.
  [ "$(tail -n1 <<<"$output")" = "$(printf 'picker\t%s' "$TEST_TMP/store/gotg-ui-out")" ]
}

@test "update self that cannot build puts the lock back and leaves the roots alone" {
  self_update_nix
  mkdir -p "$TEST_TMP/store/was-ui"
  ln -sfn "$TEST_TMP/store/was-ui" "$GOTG_UI_ROOT"
  cp "$GOTG_LIBRARY/flake.lock" "$TEST_TMP/lock-before"
  export BUILD_FAILS=1
  run gotg update self
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"nothing was changed"* ]]
  cmp -s "$GOTG_LIBRARY/flake.lock" "$TEST_TMP/lock-before"
  [ ! -e "$GOTG_LIBRARY/flake.lock.before-update" ]
  [ "$(readlink -f "$GOTG_UI_ROOT")" = "$TEST_TMP/store/was-ui" ]
}

@test "update self refuses a library it cannot write" {
  export GOTG_LIBRARY="/nix/store/zzzz-library"
  run gotg update self
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"cannot be moved from here"* ]]
  [ ! -e "$NIX_LOG" ]
}

@test "update with an argument it does not know says its usage" {
  run gotg update --frobnicate
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg update"* ]]
}
