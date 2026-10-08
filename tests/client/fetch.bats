#!/usr/bin/env bats
# service_fetch_file: one file from the byte hosts, with the catalog re-read
# once when every cached host is dead. keys.bats and firmware.bats cover it
# through their callers; these pin the helper itself.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
  load_client_libs
}

teardown() { stop_saves_service; }

setup_fetch() {
  mkdir -p "$SERVICE_FILES_DIR/switch"
  printf 'payload\n' >"$SERVICE_FILES_DIR/switch/a.bin"
}

@test "service_fetch_file takes a file from the byte host" {
  setup_fetch
  gotg refresh
  service_fetch_file switch "a.bin" "$TEST_TMP/out" 30
  [ "$(cat "$TEST_TMP/out")" = "payload" ]
}

@test "service_fetch_file re-reads the catalog when every cached host is dead" {
  setup_fetch
  gotg refresh
  jq '. + {files_url: "http://127.0.0.1:1"}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  service_fetch_file switch "a.bin" "$TEST_TMP/out" 30
  [ "$(cat "$TEST_TMP/out")" = "payload" ]
}

@test "service_fetch_file gives up after one re-read and leaves no file" {
  setup_fetch
  gotg refresh
  jq '. + {files_url: "http://127.0.0.1:1"}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  run -1 service_fetch_file switch nope.bin "$TEST_TMP/out" 30
  [ ! -e "$TEST_TMP/out" ]
}

@test "service_fetch_file refuses a file bigger than the cap it was given" {
  setup_fetch
  gotg refresh
  head -c 5000 /dev/zero >"$SERVICE_FILES_DIR/switch/big.bin"
  run -1 service_fetch_file switch big.bin "$TEST_TMP/out" 30 100
  [ ! -e "$TEST_TMP/out" ]
}
