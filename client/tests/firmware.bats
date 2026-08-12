#!/usr/bin/env bats
# Console firmware — installed before the emulator can stop to ask for it.
#
# What is worth getting right: the on-disk layout is exactly the one the
# emulator's own installer writes, one download serves every environment, and
# nothing about firmware can ever stop a launch.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  load_client_libs
}

teardown() { stop_saves_service; }

REGISTERED="config/Ryujinx/bis/system/Contents/registered"

# An environment that declares where firmware goes, as the derivation would.
fake_firmware_env() {
  local attr="${1:-env-switch}"
  mkdir -p "$GOTG_ROOTS_DIR/$attr/share/gotg"
  jq -n --arg into "$REGISTERED" \
    '{into: $into, file: "firmware.zip"}' \
    >"$GOTG_ROOTS_DIR/$attr/share/gotg/firmware.json"
}

# A firmware zip the way dumps ship: flat NCAs, a .cnmt among them, and one
# entry already in the directory shape, all in the one archive.
serve_firmware() {
  local src="$TEST_TMP/fw-src"
  mkdir -p "$src/cccc.nca"
  printf 'nca-flat' >"$src/aaaa.nca"
  printf 'nca-meta' >"$src/bbbb.cnmt.nca"
  printf 'nca-dir' >"$src/cccc.nca/00"
  mkdir -p "$SERVICE_FILES_DIR/switch"
  (cd "$src" && python3 -m zipfile -c \
    "$SERVICE_FILES_DIR/switch/firmware.zip" aaaa.nca bbbb.cnmt.nca cccc.nca)
}

registered_dir() { printf '%s/%s/%s' "$GOTG_STATE_DIR/env" "${1:-env-switch}" "$REGISTERED"; }

@test "firmware lands in the emulator's own layout: a directory per NCA, the file as 00" {
  fake_firmware_env
  serve_firmware
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -f "$(registered_dir)/aaaa.nca/00" ]
  [ -f "$(registered_dir)/cccc.nca/00" ]
  grep -q 'nca-flat' "$(registered_dir)/aaaa.nca/00"
}

@test ".cnmt is stripped from the directory name, as the emulator's installer strips it" {
  fake_firmware_env
  serve_firmware
  firmware_ensure env-switch switch
  [ -f "$(registered_dir)/bbbb.nca/00" ]
  [ ! -e "$(registered_dir)/bbbb.cnmt.nca" ]
}

@test "a second environment takes the cache, not the network" {
  fake_firmware_env env-switch
  fake_firmware_env env-switch-game
  serve_firmware
  firmware_ensure env-switch switch
  stop_saves_service
  run firmware_ensure env-switch-game switch
  [ "$status" -eq 0 ]
  [ -f "$(registered_dir env-switch-game)/aaaa.nca/00" ]
  start_saves_service
}

@test "environments share one copy — hardlinks, not duplicates" {
  fake_firmware_env env-switch
  fake_firmware_env env-switch-game
  serve_firmware
  firmware_ensure env-switch switch
  firmware_ensure env-switch-game switch
  local a b
  a="$(stat -c '%i' "$(registered_dir env-switch)/aaaa.nca/00")"
  b="$(stat -c '%i' "$(registered_dir env-switch-game)/aaaa.nca/00")"
  [ "$a" = "$b" ]
}

@test "an environment that installed firmware through the emulator seeds the rest" {
  fake_firmware_env env-switch
  fake_firmware_env env-switch-game
  # No zip on the server: what exists is one environment the emulator's own
  # dialog already installed into.
  mkdir -p "$(registered_dir env-switch)/dddd.nca"
  printf 'nca-dialog' >"$(registered_dir env-switch)/dddd.nca/00"
  run firmware_ensure env-switch-game switch
  [ "$status" -eq 0 ]
  grep -q 'nca-dialog' "$(registered_dir env-switch-game)/dddd.nca/00"
}

@test "firmware already present means nothing is fetched" {
  fake_firmware_env
  serve_firmware
  firmware_ensure env-switch switch
  stop_saves_service
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  start_saves_service
}

@test "no firmware anywhere warns and still lets the launch happen" {
  fake_firmware_env
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [[ "$output" == *"no switch firmware"* ]]
  [ ! -e "$(registered_dir)" ]
}

@test "a zip with no NCAs in it is refused, not installed" {
  fake_firmware_env
  mkdir -p "$SERVICE_FILES_DIR/switch" "$TEST_TMP/junk"
  printf 'not firmware' >"$TEST_TMP/junk/readme.txt"
  (cd "$TEST_TMP/junk" && python3 -m zipfile -c \
    "$SERVICE_FILES_DIR/switch/firmware.zip" readme.txt)
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ ! -e "$(registered_dir)" ]
}

@test "an environment that declares no firmware does nothing at all" {
  mkdir -p "$GOTG_ROOTS_DIR/env-snes/share/gotg"
  run firmware_ensure env-snes snes
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a hostile zip cannot write outside its own registered directory" {
  fake_firmware_env
  mkdir -p "$SERVICE_FILES_DIR/switch"
  # Traversal, an absolute path, a symlink aimed at the system, and the deep
  # folder shape real dumps ship in. python3 -m zipfile contains all of them —
  # the property this test exists to keep against a future switch to unzip,
  # which does not.
  python3 - "$SERVICE_FILES_DIR/switch/firmware.zip" <<'EOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("../../escape.nca", "traversal")
    z.writestr("/abs/rooted.nca", "rooted")
    zi = zipfile.ZipInfo("link.nca")
    zi.external_attr = 0o120777 << 16
    z.writestr(zi, "/etc/passwd")
    z.writestr("Firmware 18.1.0/nx/eeee.nca/00", "deep")
EOF
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -f "$(registered_dir)/escape.nca/00" ]
  [ -f "$(registered_dir)/rooted.nca/00" ]
  grep -q 'deep' "$(registered_dir)/eeee.nca/00"
  [ ! -e "$TEST_TMP/escape.nca" ]
  [ ! -e "$GOTG_STATE_DIR/escape.nca" ]
  [ ! -L "$(registered_dir)/link.nca/00" ]
}

@test "a zip that inflates past the cap is refused before it inflates" {
  fake_firmware_env
  serve_firmware
  GOTG_FIRMWARE_MAX_BYTES=4 run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [[ "$output" == *"no switch firmware"* ]]
  [ ! -e "$(registered_dir)" ]
}

@test "a truncated zip is refused with nothing left behind, and a good one heals it" {
  fake_firmware_env
  serve_firmware
  head -c 20 "$SERVICE_FILES_DIR/switch/firmware.zip" \
    >"$TEST_TMP/firmware.zip.t"
  mv "$TEST_TMP/firmware.zip.t" "$SERVICE_FILES_DIR/switch/firmware.zip"
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [[ "$output" == *"no switch firmware"* ]]
  [ ! -e "$(registered_dir)" ]
  [ ! -e "$GOTG_STATE_DIR/firmware/switch" ]
  [ ! -e "$GOTG_STATE_DIR/firmware/switch.part" ]
  # The server is fixed; the next launch must not remember the bad fetch.
  serve_firmware
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -f "$(registered_dir)/aaaa.nca/00" ]
}

@test "stale staging from an interrupted run is swept, never installed" {
  fake_firmware_env
  serve_firmware
  mkdir -p "$GOTG_STATE_DIR/firmware/switch.part/junk.nca"
  printf 'stale' >"$GOTG_STATE_DIR/firmware/switch.part/junk.nca/00"
  mkdir -p "$(registered_dir).part/junk.nca"
  printf 'stale' >"$(registered_dir).part/junk.nca/00"
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_STATE_DIR/firmware/switch.part" ]
  [ ! -e "$(registered_dir).part" ]
  [ ! -e "$(registered_dir)/junk.nca" ]
  [ -f "$(registered_dir)/aaaa.nca/00" ]
}

@test "an empty cache directory — an interrupted first fetch — is no cache at all" {
  fake_firmware_env
  serve_firmware
  mkdir -p "$GOTG_STATE_DIR/firmware/switch"
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ -f "$(registered_dir)/aaaa.nca/00" ]
}

@test "a cache the hardlink cannot reach is copied instead" {
  # A state dir on another filesystem: cp -al fails there, and the fallback
  # copy is what installs. Forced here by shadowing cp, since the test tmpdir
  # is one filesystem.
  fake_firmware_env
  serve_firmware
  cp() { if [ "$1" = "-al" ]; then return 1; fi; command cp "$@"; }
  run firmware_ensure env-switch switch
  unset -f cp
  [ "$status" -eq 0 ]
  grep -q 'nca-flat' "$(registered_dir)/aaaa.nca/00"
  local a b
  a="$(stat -c '%i' "$GOTG_STATE_DIR/firmware/switch/aaaa.nca/00")"
  b="$(stat -c '%i' "$(registered_dir)/aaaa.nca/00")"
  [ "$a" != "$b" ]
}

@test "a copy that cannot happen at all warns and never blocks the launch" {
  fake_firmware_env
  serve_firmware
  cp() { return 1; }
  run firmware_ensure env-switch switch
  unset -f cp
  [ "$status" -eq 0 ]
  [[ "$output" == *"could not place firmware"* ]]
  [ ! -e "$(registered_dir)" ]
  [ ! -e "$(registered_dir).part" ]
}

@test "adoption skips an environment whose registered directory is empty" {
  fake_firmware_env env-switch-game
  # env-aaa sorts first in the glob and has nothing; env-bbb has the goods.
  mkdir -p "$GOTG_STATE_DIR/env/env-aaa/$REGISTERED"
  mkdir -p "$GOTG_STATE_DIR/env/env-bbb/$REGISTERED/dddd.nca"
  printf 'nca-dialog' >"$GOTG_STATE_DIR/env/env-bbb/$REGISTERED/dddd.nca/00"
  run firmware_ensure env-switch-game switch
  [ "$status" -eq 0 ]
  grep -q 'nca-dialog' "$(registered_dir env-switch-game)/dddd.nca/00"
}

@test "a sibling's interrupted staging is not mistaken for installable firmware" {
  fake_firmware_env env-switch-game
  mkdir -p "$GOTG_STATE_DIR/env/env-aaa/$REGISTERED.part/dddd.nca"
  printf 'stale' >"$GOTG_STATE_DIR/env/env-aaa/$REGISTERED.part/dddd.nca/00"
  run firmware_ensure env-switch-game switch
  [ "$status" -eq 0 ]
  [[ "$output" == *"no switch firmware"* ]]
  [ ! -e "$(registered_dir env-switch-game)" ]
}

@test "a corrupt firmware.json is treated as no declaration" {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  printf 'not json' >"$GOTG_ROOTS_DIR/env-switch/share/gotg/firmware.json"
  serve_firmware
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [ ! -e "$(registered_dir)" ]
}

@test "a manifest that points outside the environment is refused" {
  mkdir -p "$GOTG_ROOTS_DIR/env-switch/share/gotg"
  jq -n '{into: "../../../../etc", file: "firmware.zip"}' \
    >"$GOTG_ROOTS_DIR/env-switch/share/gotg/firmware.json"
  serve_firmware
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  [[ "$output" == *"refusing firmware path"* ]]
}

@test "two launches racing on an empty cache still end with sound firmware" {
  # The regression net for the platform lock: before it, two racers staged
  # into the same fixed .part paths and could publish each other's half — a
  # partial cache that looks populated and is never retried.
  fake_firmware_env env-switch
  fake_firmware_env env-switch-game
  serve_firmware
  local a b
  firmware_ensure env-switch switch &
  a=$!
  firmware_ensure env-switch-game switch &
  b=$!
  # The racers by pid, not a bare `wait` — that would also wait on the mock
  # server setup put in the background of this same shell, forever.
  wait "$a" "$b"
  run firmware_ensure env-switch switch
  [ "$status" -eq 0 ]
  local nca
  for nca in aaaa bbbb cccc; do
    [ -f "$GOTG_STATE_DIR/firmware/switch/$nca.nca/00" ]
  done
}
