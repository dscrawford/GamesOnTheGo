#!/usr/bin/env bats
# The steps of `gotg login`, asked one at a time. tokens.bats runs the whole
# command against a real service; these are the decisions that need none.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
  CODE="gotgi_$(printf 'a%.0s' {1..43})"
}

@test "login step: --server and --claim are read, and a missing value is refused" {
  _login_parse_args --server https://gotg.example.com --claim "https://gotg.example.com/claim/$CODE/"
  [ "$LOGIN_SERVER" = https://gotg.example.com ]
  [ "$LOGIN_CLAIM" = "https://gotg.example.com/claim/$CODE" ]
  _login_parse_args
  [ -z "$LOGIN_SERVER" ] && [ -z "$LOGIN_CLAIM" ]
  run _login_parse_args --server
  [ "$status" -ne 0 ]
  [[ "$output" == *"usage: gotg login"* ]]
  run _login_parse_args --claim
  [ "$status" -ne 0 ]
}

@test "login step: a claim link is split into service and code, tracking dropped" {
  _login_claim_parts "https://gotg.example.com/claim/$CODE?utm_source=chat"
  [ "$LOGIN_URL" = https://gotg.example.com ]
  [ "$LOGIN_CODE" = "$CODE" ]
}

@test "login step: a link that is not a claim, or is cleartext to the internet, is refused" {
  run _login_claim_parts "https://gotg.example.com/other/$CODE"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not a claim url"* ]]
  run _login_claim_parts "https://gotg.example.com/claim/gotgi_short"
  [[ "$output" == *"not a claim url"* ]]
  run _login_claim_parts "http://gotg.example.com/claim/$CODE"
  [ "$status" -ne 0 ]
  [[ "$output" == *"hands the token to the network"* ]]
}

@test "login step: a token with characters no bearer token uses is refused" {
  _login_check_token "abc.DEF-123~+/="
  run _login_check_token 'ab c'
  [ "$status" -ne 0 ]
  [[ "$output" == *"characters no bearer token uses"* ]]
  run _login_check_token $'ab"\nheader = "x'
  [ "$status" -ne 0 ]
}

@test "login step: the saved file keeps what was there and names who you are, if told" {
  export GOTG_API_FILE="$TEST_TMP/api.json"
  printf '{"other": 1}' >"$GOTG_API_FILE"
  run --separate-stderr _login_save https://gotg.example.com tok123 alice
  [ "$status" -eq 0 ]
  [ "$(jq -c . "$GOTG_API_FILE")" = '{"other":1,"url":"https://gotg.example.com","token":"tok123","name":"alice"}' ]
  [ "$(stat -c %a "$GOTG_API_FILE")" = 600 ]
  [[ "$stderr" == *"you are alice"* ]]
  run --separate-stderr _login_save https://gotg.example.com tok123 ""
  [ "$(jq -r '.name // "none"' "$GOTG_API_FILE")" = alice ]  # merge keeps the earlier name
  [[ "$stderr" != *"you are"* ]]
}

@test "login step: a leftover File Browser password is pointed out" {
  export GOTG_CONFIG_FILE="$TEST_TMP/config.json"
  run --separate-stderr _login_warn_old_password
  [ -z "$stderr" ]
  printf '{"password": "x"}' >"$GOTG_CONFIG_FILE"
  run --separate-stderr _login_warn_old_password
  [[ "$stderr" == *"old File Browser password"* ]]
}
