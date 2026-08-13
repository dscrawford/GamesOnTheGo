#!/usr/bin/env bats
# Per-person tokens from the client's side: claimed once, administered by name.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
}

teardown() { stop_saves_service; }

admin() {
  GOTG_ADMIN_TOKEN="admin-token" gotg admin "$@"
}

invite_code() {
  GOTG_ADMIN_TOKEN="admin-token" run --separate-stderr "$GOTG_BIN" admin invite "$1"
  [ "$status" -eq 0 ] || {
    echo "invite failed: $stderr" >&2
    return 1
  }
  grep -o 'gotgi_[A-Za-z0-9_-]*' <<<"$output" | head -1
}

@test "login --claim exchanges the code and writes the whole api.json" {
  local code
  code="$(invite_code alice-deck)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"
  [ "$status" -eq 0 ]

  [ "$(jq -r .url "$GOTG_CONFIG_DIR/api.json")" = "$GOTG_SERVICE_URL" ]
  [ "$(jq -r .name "$GOTG_CONFIG_DIR/api.json")" = "alice-deck" ]
  [[ "$(jq -r .token "$GOTG_CONFIG_DIR/api.json")" == gotg_* ]]
  [ "$(stat -c '%a' "$GOTG_CONFIG_DIR/api.json")" = "600" ]

  # The claimed token really authenticates.
  gotg saves status
  [ "$status" -eq 0 ]
}

@test "the minted token fits the charset login itself enforces" {
  local code
  code="$(invite_code judy)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"
  [[ "$(jq -r .token "$GOTG_CONFIG_DIR/api.json")" =~ ^[A-Za-z0-9._~+/=-]+$ ]]
}

@test "a claim url with a trailing slash still derives the base url" {
  local code
  code="$(invite_code bob)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code/"
  [ "$status" -eq 0 ]
  [ "$(jq -r .url "$GOTG_CONFIG_DIR/api.json")" = "$GOTG_SERVICE_URL" ]
}

@test "a reused claim link says whose fault it is not" {
  local code
  code="$(invite_code carol)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"
  [ "$status" -eq 0 ]

  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"already used or has expired"* ]]
}

@test "a bogus claim url is an error, not a written config" {
  rm -f "$GOTG_CONFIG_DIR/api.json"
  gotg login --claim "$GOTG_SERVICE_URL/claim/gotgi_nothing"
  [ "$status" -ne 0 ]
  [ ! -e "$GOTG_CONFIG_DIR/api.json" ]
}

@test "admin lists what it minted and revokes by name" {
  local code
  code="$(invite_code dave)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"

  admin tokens
  [ "$status" -eq 0 ]
  [[ "$output" == *"dave"* ]]
  [[ "$output" == *"gotg_…"* ]]

  gotg refresh
  [ "$status" -eq 0 ]

  admin revoke dave
  [ "$status" -eq 0 ]

  # A built env, so the pull actually calls home — and dies on the 401.
  fake_env env-snes '["saves/**"]'
  gotg saves pull --all
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"revoked or expired"* ]]
}

@test "admin without its token says where to find it" {
  run --separate-stderr env -u GOTG_ADMIN_TOKEN "$GOTG_BIN" admin tokens
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"GOTG_ADMIN_TOKEN"* ]]
}

@test "admin help names its commands" {
  gotg admin --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"invite"* ]]
  [[ "$output" == *"revoke"* ]]
  [[ "$output" == *"tokens"* ]]
}

@test "login --claim merges into an existing api.json instead of rewriting it" {
  mkdir -p "$GOTG_CONFIG_DIR"
  chmod 700 "$GOTG_CONFIG_DIR"
  jq -n --arg url "$GOTG_SERVICE_URL" \
    '{url: $url, token: "test-token", steamgriddb_base: "kept-field"}' \
    >"$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"

  local code
  code="$(invite_code erin-deck)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"
  [ "$status" -eq 0 ]

  [ "$(jq -r .steamgriddb_base "$GOTG_CONFIG_DIR/api.json")" = "kept-field" ]
  [ "$(jq -r .name "$GOTG_CONFIG_DIR/api.json")" = "erin-deck" ]
  [[ "$(jq -r .token "$GOTG_CONFIG_DIR/api.json")" == gotg_* ]]
  [ "$(stat -c '%a' "$GOTG_CONFIG_DIR/api.json")" = "600" ]
}

@test "a claim url with query junk still claims and derives the base url" {
  local code
  code="$(invite_code hank)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code?utm_source=chat"
  [ "$status" -eq 0 ]
  [ "$(jq -r .url "$GOTG_CONFIG_DIR/api.json")" = "$GOTG_SERVICE_URL" ]
}

@test "admin invite --ttl takes 1-999 whole days and nothing else" {
  local bad
  for bad in 0 -1 abc 1000 1.5 ""; do
    admin invite kate --ttl "$bad"
    [ "$status" -ne 0 ]
    [[ "$stderr" == *"1-999"* ]]
  done
  admin invite kate --ttl
  [ "$status" -ne 0 ]

  admin invite kate --ttl 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"/claim/gotgi_"* ]]
  [[ "$stderr" == *"30 day(s)"* ]]
}

@test "admin reaches the url in api.json before GOTG_SERVICE_URL" {
  write_api_config
  run --separate-stderr env GOTG_ADMIN_TOKEN="admin-token" \
    GOTG_SERVICE_URL="http://127.0.0.1:1" "$GOTG_BIN" admin tokens
  [ "$status" -eq 0 ]
  [[ "$output" == *"NAME"* ]]
}

@test "saves status reports a refused token as a fact, not a failure" {
  fake_env env-snes '["saves/**"]'
  write_api_config "gotg_bogus"
  gotg saves status --all
  [ "$status" -eq 0 ]
  [[ "$output" == *"token refused"* ]]
  [[ "$output" == *"gotg login"* ]]
}

@test "setup's probe catches a token revoked since login" {
  local code
  code="$(invite_code eve)"
  gotg login --claim "$GOTG_SERVICE_URL/claim/$code"
  [ "$status" -eq 0 ]
  admin revoke eve
  [ "$status" -eq 0 ]

  gotg saves setup
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"revoked or expired"* ]]
}

@test "a refused token cannot stop a launch: the pre-play pull shrugs" {
  load_client_libs
  # shellcheck source=/dev/null
  source "$GOTG_LIB/cmd-saves.sh"
  saves_tmp_init
  fake_env env-snes '["saves/**"]'
  write_api_config "gotg_bogus"
  run saves_pull_auto env-snes
  [ "$status" -eq 0 ]
}
