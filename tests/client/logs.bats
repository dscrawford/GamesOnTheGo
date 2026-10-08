#!/usr/bin/env bats
# Opt-in session logs: the switch, the redaction, the bundle, the upload, and
# the capture on the play path. The upload is tested twice: against a curl
# stand-in (what is sent) and against the real service (what it keeps).

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  write_api_config_offline
  load_client_libs
}

teardown() {
  stop_saves_service
}

# A login whose server nothing listens on: a push must fail and keep its files.
write_api_config_offline() {
  mkdir -p "$GOTG_CONFIG_DIR"
  chmod 700 "$GOTG_CONFIG_DIR"
  jq -n '{url: "http://127.0.0.1:1", token: "test-token"}' >"$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
}

# A finished session directory, the way the watcher leaves one.
make_session() {
  local id="${1:-20261008T165221Z-env-n64-usa_zelda}" ended="${2-2026-10-08T17:00:00Z}"
  local dir="$GOTG_STATE_DIR/sessions/$id"
  mkdir -p "$dir"
  jq -n --arg e "$ended" --arg a "${id#*Z-}" \
    '{version: 1, attr: $a, platform: "n64", id: "usa.zelda", variant: "", machine: "desktop",
      gotg: "checkout", started: "2026-10-08T16:52:21Z", console_truncated: false}
     + (if $e == "" then {} else {ended: $e} end)' >"$dir/session.json"
  printf 'launching zelda\nthe game said hello\n' >"$dir/console.log"
  printf '%s' "$dir"
}

# A curl that records what it was asked and answers like the service does.
stub_curl() {
  local code="${1:-200}"
  export CURL_ARGS="$TEST_TMP/curl.args" CURL_BODY="$TEST_TMP/curl.body" STUB_CODE="$code"
  : >"$CURL_ARGS"
  make_stub "$TEST_TMP/bin/curl" \
    '[[ "$*" == */logs/* ]] || exec '"$(command -v curl)"' "$@"' \
    'cat >/dev/null' \
    'out=""; prev=""' \
    'for a in "$@"; do' \
    '  [[ "$prev" == "-o" ]] && out="$a"' \
    '  [[ "$a" == @* && "$prev" == "--data-binary" ]] && cp "${a#@}" "$CURL_BODY"' \
    '  prev="$a"' \
    'done' \
    'printf "%s\n" "$*" >>"$CURL_ARGS"' \
    '[[ -z "$out" ]] || echo "{\"kept\": 10, \"sessions\": 1, \"dropped\": []}" >"$out"' \
    'printf "%s" "$STUB_CODE"'
  export GOTG_CURL="$TEST_TMP/bin/curl"
}

# ---------------------------------------------------------------- the switch

@test "logs status is off until it is turned on" {
  gotg logs status
  [ "$status" -eq 0 ]
  [ "$output" = "off" ]
}

@test "logs on and off flip the config, and keep what else is in it" {
  mkdir -p "$GOTG_CONFIG_DIR"
  echo '{"library": "/x"}' >"$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"
  gotg logs on
  [ "$status" -eq 0 ]
  [ "$(jq -r .share_logs "$GOTG_CONFIG_FILE")" = "true" ]
  [ "$(jq -r .library "$GOTG_CONFIG_FILE")" = "/x" ]
  gotg logs status
  [ "$output" = "on" ]
  gotg logs off
  [ "$(jq -r .share_logs "$GOTG_CONFIG_FILE")" = "false" ]
  gotg logs status
  [ "$output" = "off" ]
}

@test "logs with an unknown subcommand says what there is" {
  gotg logs banana
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"usage: gotg logs"* ]]
}

# ------------------------------------------------------------------ redaction

redact() { printf '%s\n' "$1" | logs_redact; }

@test "redaction: a bearer token" {
  [ "$(redact 'Authorization: Bearer abc123.def-456_x')" = "Authorization: Bearer <redacted>" ]
}

@test "redaction: token= in a url or a command line" {
  [ "$(redact 'GET /x?token=sekrit&a=1 HTTP/1.1')" = "GET /x?token=<redacted>&a=1 HTTP/1.1" ]
}

@test "redaction: a json token" {
  [ "$(redact '{"url": "u", "token": "s3kr1t value"}')" = '{"url": "u", "token": "<redacted>"}' ]
}

@test "redaction: a long hex credential" {
  [ "$(redact 'key 0123456789abcdef0123456789abcdef0123 end')" = "key <redacted> end" ]
}

@test "redaction: a long base64 credential" {
  [ "$(redact 'k=Zm9vQmFyQmF6MTIzNDU2Nzg5MEFCQ0RFRkdISUpLTE1O ok')" = "k=<redacted> ok" ]
}

@test "redaction: leaves store paths, attrs and short words alone" {
  local line='/nix/store/0123456789abcdfghijklmnpqrsvwxyz-gotg-1.0/bin env-n64-usa_paper_mario-paperboat abc123'
  [ "$(redact "$line")" = "$line" ]
}

# --------------------------------------------------------------------- bundle

@test "a bundle is zstd and holds the session, the console and danstick's log" {
  local dir
  dir="$(make_session)"
  printf 'danstick says\n' >"$dir/danstick.log"
  run logs_bundle "$dir" "$TEST_TMP/b.tar.zst"
  [ "$status" -eq 0 ]
  [ "$(head -c4 "$TEST_TMP/b.tar.zst" | od -An -tx1 | tr -d ' \n')" = "28b52ffd" ]
  run tar --zstd -tf "$TEST_TMP/b.tar.zst"
  [[ "$output" == *session.json* && "$output" == *console.log* && "$output" == *danstick.log* ]]
}

@test "a bundle has no danstick.log when there was none" {
  local dir
  dir="$(make_session)"
  logs_bundle "$dir" "$TEST_TMP/b.tar.zst"
  run tar --zstd -tf "$TEST_TMP/b.tar.zst"
  [[ "$output" != *danstick.log* ]]
}

@test "a bundle is redacted and the directory is left as it was" {
  local dir
  dir="$(make_session)"
  echo 'Authorization: Bearer topsecretvalue' >>"$dir/console.log"
  logs_bundle "$dir" "$TEST_TMP/b.tar.zst"
  mkdir "$TEST_TMP/x"
  tar --zstd -C "$TEST_TMP/x" -xf "$TEST_TMP/b.tar.zst"
  [[ "$(cat "$TEST_TMP/x/console.log")" != *topsecretvalue* ]]
  [[ "$(cat "$TEST_TMP/x/console.log")" == *"Bearer <redacted>"* ]]
  grep -q topsecretvalue "$dir/console.log"
}

@test "a bundle's session.json carries the contract's fields and an end time" {
  local dir
  dir="$(make_session 20261008T165221Z-env-n64-usa_zelda "")"
  logs_bundle "$dir" "$TEST_TMP/b.tar.zst"
  tar --zstd -xOf "$TEST_TMP/b.tar.zst" session.json >"$TEST_TMP/s.json"
  run jq -e '.version == 1 and .attr == "env-n64-usa_zelda" and .platform == "n64"
    and (.ended | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$")) and .console_truncated == false' "$TEST_TMP/s.json"
  [ "$status" -eq 0 ]
}

@test "a console over the cap is cut and says so" {
  local dir
  dir="$(make_session)"
  head -c 3000 /dev/zero | tr '\0' 'z' >"$dir/console.log"
  GOTG_LOGS_CONSOLE_CAP=1000 logs_bundle "$dir" "$TEST_TMP/b.tar.zst"
  tar --zstd -xOf "$TEST_TMP/b.tar.zst" console.log | wc -c | awk '{exit !($1 >= 1000 && $1 <= 1001)}'
  [ "$(tar --zstd -xOf "$TEST_TMP/b.tar.zst" session.json | jq .console_truncated)" = "true" ]
}

# --------------------------------------------------------------------- upload

@test "push sends the bundle to /logs/<id> with the attr and device, then removes the directory" {
  stub_curl
  local dir
  dir="$(make_session)"
  GOTG_DEVICE_NAME="daniel-deck" gotg logs push "$dir"
  [ "$status" -eq 0 ]
  [ ! -e "$dir" ]
  grep -q -- "-X PUT" "$CURL_ARGS"
  grep -q "/logs/20261008T165221Z-env-n64-usa_zelda" "$CURL_ARGS"
  grep -q "X-Gotg-Attr: env-n64-usa_zelda" "$CURL_ARGS"
  grep -q "X-Gotg-Device: daniel-deck" "$CURL_ARGS"
  [ "$(head -c4 "$CURL_BODY" | od -An -tx1 | tr -d ' \n')" = "28b52ffd" ]
}

@test "push to a dead service keeps the directory and fails" {
  local dir
  dir="$(make_session)"
  gotg logs push "$dir"
  [ "$status" -ne 0 ]
  [ -d "$dir" ]
}

@test "push the service refuses keeps the directory" {
  stub_curl 503
  local dir
  dir="$(make_session)"
  gotg logs push "$dir"
  [ "$status" -ne 0 ]
  [ -d "$dir" ]
}

@test "push --pending retries what a failed push kept" {
  local dir
  dir="$(make_session)"
  gotg logs push "$dir"
  [ -d "$dir" ]
  stub_curl
  gotg logs on
  gotg logs push --pending
  [ "$status" -eq 0 ]
  [ ! -e "$dir" ]
}

@test "push --pending leaves a session whose game is still running" {
  stub_curl
  gotg logs on
  local dir
  dir="$(make_session 20261008T165221Z-env-n64-usa_zelda "")"
  sleep 30 &
  local sleeper=$!
  echo "$sleeper" >"$dir/pid"
  gotg logs push --pending
  kill "$sleeper"
  [ -d "$dir" ]
  [ ! -s "$CURL_ARGS" ]
}

@test "push --pending finishes a session whose game is gone without a watcher" {
  stub_curl
  gotg logs on
  local dir
  dir="$(make_session 20261008T165221Z-env-n64-usa_zelda "")"
  echo 2147483 >"$dir/pid"
  gotg logs push --pending
  [ "$status" -eq 0 ]
  [ ! -e "$dir" ]
}

@test "push --pending uploads nothing when sharing is off" {
  stub_curl
  local dir
  dir="$(make_session)"
  gotg logs push --pending
  [ -d "$dir" ]
  [ ! -s "$CURL_ARGS" ]
}

@test "only the ten newest pending sessions are kept" {
  local i
  for i in $(seq 10 23); do make_session "202610${i}T000000Z-env-n64-usa_zelda" >/dev/null; done
  logs_prune_pending
  [ "$(ls "$GOTG_STATE_DIR/sessions" | wc -l)" -eq 10 ]
  [ ! -d "$GOTG_STATE_DIR/sessions/20261010T000000Z-env-n64-usa_zelda" ]
  [ -d "$GOTG_STATE_DIR/sessions/20261023T000000Z-env-n64-usa_zelda" ]
}

@test "push to the real service is kept there, and the directory goes" {
  start_saves_service
  write_api_config
  local dir
  dir="$(make_session)"
  GOTG_DEVICE_NAME="daniel-deck" gotg logs push "$dir"
  [ "$status" -eq 0 ]
  [ ! -e "$dir" ]
  ls "$TEST_TMP"/service-logs/*/20261008T165221Z-env-n64-usa_zelda.tar.zst
}

# ------------------------------------------------------------------ play path

setup_play() {
  start_saves_service
  write_api_config
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/n64.nix"
  fake_env env-n64
  mkdir -p "$TEST_TMP/data"
  echo '{}' >"$TEST_TMP/data/overrides.json"
  export GOTG_DATA="$TEST_TMP/data"
  add_game n64 "usa.zelda.z64" "rom" "Zelda"
  gotg refresh
  use_library
  make_stub "$TEST_TMP/fake-killswitch" 'exit 0'
  export GOTG_KILLSWITCH_BIN="$TEST_TMP/fake-killswitch"
  stub_curl
}

pushed() { [ -s "$CURL_ARGS" ]; }

@test "play with sharing on captures the console and the watcher uploads after the game" {
  setup_play
  gotg logs on
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  wait_for 10 pushed
  grep -q "/logs/2" "$CURL_ARGS"
  # The tar in the upload has what the player saw.
  tar --zstd -xOf "$CURL_BODY" console.log | grep -q "env-n64 launched with"
  tar --zstd -xOf "$CURL_BODY" session.json | jq -e '.id == "usa.zelda" and .platform == "n64" and .ended != null'
  wait_for 5 test -z "$(ls -A "$GOTG_STATE_DIR/sessions" 2>/dev/null)"
}

@test "play still prints what it printed with sharing on" {
  setup_play
  gotg logs on
  gotg play usa.zelda
  [[ "$output" == *"env-n64 launched with"* ]]
}

@test "play with sharing off writes nothing and uploads nothing" {
  setup_play
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_STATE_DIR/sessions" ]
  sleep 0.5
  [ ! -s "$CURL_ARGS" ]
}

@test "a service that cannot take the upload does not hold up a launch, and the session is kept" {
  setup_play
  export STUB_CODE=503
  gotg logs on
  gotg play usa.zelda
  [ "$status" -eq 0 ]
  wait_for 10 test -n "$(ls -A "$GOTG_STATE_DIR/sessions" 2>/dev/null)"
  wait_for 15 pushed
  [ "$(jq -r '.ended // empty' "$GOTG_STATE_DIR"/sessions/*/session.json)" != "" ]
}

# ----------------------------------------------------------------- admin CLI

@test "admin logs get refuses an id or user that is not one" {
  export GOTG_ADMIN_TOKEN=x
  gotg admin logs get alice ../../etc
  [ "$status" -ne 0 ]
  gotg admin logs get ../alice 20261008T165221Z-env-n64-usa_zelda
  [ "$status" -ne 0 ]
}

@test "admin logs without the admin token says so" {
  unset GOTG_ADMIN_TOKEN
  gotg admin logs
  [ "$status" -ne 0 ]
  [[ "$stderr" == *GOTG_ADMIN_TOKEN* ]]
}

seed_logs() {
  start_saves_service
  write_api_config
  export GOTG_ADMIN_TOKEN="admin-token"
  local dir
  dir="$(make_session)"
  GOTG_DEVICE_NAME="daniel-deck" gotg logs push "$dir"
}

@test "admin logs lists users, then one user's sessions" {
  seed_logs
  gotg admin logs
  [ "$status" -eq 0 ]
  [[ "$output" == *legacy* ]]
  gotg admin logs legacy
  [[ "$output" == *20261008T165221Z-env-n64-usa_zelda* && "$output" == *daniel-deck* ]]
}

@test "admin logs get unpacks the bundle and prints where" {
  seed_logs
  cd "$TEST_TMP"
  gotg admin logs get legacy 20261008T165221Z-env-n64-usa_zelda
  [ "$status" -eq 0 ]
  [ -f "$TEST_TMP/20261008T165221Z-env-n64-usa_zelda/console.log" ]
  [[ "$output" == *20261008T165221Z-env-n64-usa_zelda* ]]
}

@test "admin logs rm removes a session" {
  seed_logs
  gotg admin logs rm legacy 20261008T165221Z-env-n64-usa_zelda
  [ "$status" -eq 0 ]
  gotg admin logs legacy
  [[ "$output" != *20261008T165221Z-env-n64-usa_zelda* ]]
}
