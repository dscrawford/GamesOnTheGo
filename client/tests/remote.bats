#!/usr/bin/env bats
# The blob store, against the stand-in File Browser over real HTTP.
#
# These call blob_* directly rather than going through a command: the point is
# the five-operation contract every backend has to keep, separately from
# anything that decides which save wins.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
  load_client_libs
  export GOTG_SAVES_BACKEND=filebrowser
  KEY="env-n64/gen/000042-3f9a1c2b4d5e.tar.zst"
  mkdir -p "$SERVER_ROOT/Games/.gotg"
}

teardown() {
  stop_server
}

@test "what comes back is byte for byte what went up" {
  head -c 4096 /dev/urandom >"$TEST_TMP/bundle"
  blob_put "$TEST_TMP/bundle" "$KEY"
  blob_get "$KEY" "$TEST_TMP/back"
  cmp "$TEST_TMP/bundle" "$TEST_TMP/back"
}

@test "a bundle is only visible at its real name once it is complete" {
  printf 'contents' >"$TEST_TMP/bundle"
  blob_put "$TEST_TMP/bundle" "$KEY"
  # Uploaded beside the final name and renamed into place, so nothing partial is
  # ever readable there — and no staging file is left behind.
  [ ! -e "$SERVER_ROOT/Games/.gotg/saves/env-n64/gen/000042-3f9a1c2b4d5e.tar.zst.part" ]
  [ -f "$SERVER_ROOT/Games/.gotg/saves/env-n64/gen/000042-3f9a1c2b4d5e.tar.zst" ]
}

@test "writing over an existing key replaces it" {
  printf 'first' >"$TEST_TMP/a"
  printf 'second' >"$TEST_TMP/b"
  blob_put "$TEST_TMP/a" "env-n64/latest.json"
  blob_put "$TEST_TMP/b" "env-n64/latest.json"
  blob_get "env-n64/latest.json" "$TEST_TMP/back"
  [ "$(cat "$TEST_TMP/back")" = "second" ]
}

@test "an upload without override is a conflict, which is why ours always overrides" {
  # filebrowser answers 409 rather than overwriting. Uploads here go to a .part
  # name we own, and a stale one from an interrupted push has to be writable, so
  # every upload passes override=true; this pins the behaviour that makes it
  # necessary.
  printf 'first' >"$TEST_TMP/a"
  blob_put "$TEST_TMP/a" "env-n64/latest.json"

  run curl -sS -o /dev/null -w '%{http_code}' -H 'X-Auth: test-token-12345' \
    --data-binary "@$TEST_TMP/a" \
    -X POST "$GOTG_SERVER_URL/api/resources/Games/.gotg/saves/env-n64/latest.json"
  [ "$output" = "409" ]
}

@test "stat reports the size and the server's own checksum" {
  head -c 1234 /dev/urandom >"$TEST_TMP/bundle"
  blob_put "$TEST_TMP/bundle" "$KEY"

  run blob_stat "$KEY"
  [ "$status" -eq 0 ]
  # Verifying a remote bundle without downloading it is the whole reason to ask.
  [ "$output" = "1234 $(sha256sum "$TEST_TMP/bundle" | cut -d' ' -f1)" ]
}

@test "stat of something never pushed fails without being an error" {
  run blob_stat "env-n64/latest.json"
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "list gives sorted names, and nothing for a directory that never existed" {
  printf 'x' >"$TEST_TMP/f"
  blob_put "$TEST_TMP/f" "env-n64/gen/000002-bbbbbbbbbbbb.tar.zst"
  blob_put "$TEST_TMP/f" "env-n64/gen/000001-aaaaaaaaaaaa.tar.zst"
  blob_put "$TEST_TMP/f" "env-n64/gen/000010-cccccccccccc.tar.zst"

  run blob_list "env-n64/gen"
  [ "$status" -eq 0 ]
  # Zero padding is what makes lexical order the same as numeric order, which is
  # all any of these listings give you to sort by.
  [ "${lines[0]}" = "000001-aaaaaaaaaaaa.tar.zst" ]
  [ "${lines[1]}" = "000002-bbbbbbbbbbbb.tar.zst" ]
  [ "${lines[2]}" = "000010-cccccccccccc.tar.zst" ]

  run blob_list "env-snes/gen"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "deleting is idempotent" {
  printf 'x' >"$TEST_TMP/f"
  blob_put "$TEST_TMP/f" "$KEY"
  blob_delete "$KEY"
  [ ! -e "$SERVER_ROOT/Games/.gotg/saves/env-n64/gen/000042-3f9a1c2b4d5e.tar.zst" ]
  # Retention deletes what it believes is there; a race with another device
  # removing the same generation must not be an error.
  blob_delete "$KEY"
}

@test "a key that is not one of the two shapes never becomes a request" {
  printf 'x' >"$TEST_TMP/f"
  local bad
  for bad in \
    "env-n64/../../../etc/passwd" \
    "env-n64/gen/../../latest.json" \
    "../env-n64/latest.json" \
    "env-n64/gen/42-3f9a1c2b4d5e.tar.zst" \
    "n64/latest.json" \
    "env-n64/latest.json extra"; do
    run bash -c "source '$GOTG_LIB/common.sh'; validate_blob_key '$bad'"
    [ "$status" -ne 0 ] || {
      echo "accepted a bad key: $bad" >&2
      return 1
    }
  done
}
