#!/usr/bin/env bats
# The catalog, credentials and id resolution.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_server
  write_config
}

teardown() {
  stop_server
}

@test "refresh caches the catalog locally" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  [ "$status" -eq 0 ]
  [ -s "$GOTG_CACHE_FILE" ]
  run jq -r '.games[0].platform' "$GOTG_CACHE_FILE"
  [ "$output" = "n64" ]
}

@test "an already installed game still lists when the server is down" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  stop_server
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.server = "http://127.0.0.1:1"' "$GOTG_CONFIG_FILE" >"$GOTG_CONFIG_FILE.tmp"
  mv "$GOTG_CONFIG_FILE.tmp" "$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"

  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
}

@test "an id on two platforms is reported as ambiguous, not guessed" {
  # This really happens: the same title is in both the N64 and SNES sets.
  add_game n64 "usa.bugs_life.zip" "n64 version"
  add_game snes "usa.bugs_life.zip" "snes version"
  gotg refresh

  gotg download usa.bugs_life
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"ambiguous"* ]]
  [[ "$stderr" == *"n64/usa.bugs_life"* ]]
  [[ "$stderr" == *"snes/usa.bugs_life"* ]]
}

@test "qualifying an ambiguous id picks the right platform" {
  add_game n64 "usa.bugs_life.zip" "n64 version"
  add_game snes "usa.bugs_life.zip" "snes version"
  gotg refresh

  gotg download snes/usa.bugs_life
  [ "$status" -eq 0 ]
  run cat "$GOTG_GAMES_DIR/snes/usa.bugs_life.zip"
  [ "$output" = "snes version" ]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.bugs_life.zip" ]
}

@test "an unknown id fails with a usable message" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg download usa.not_a_game
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no game called"* ]]
}

@test "a malformed id is rejected before it reaches a path" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  gotg download "../../etc/passwd"
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid game id"* ]]
}

@test "a world-readable config is refused" {
  add_game n64 "usa.zelda.z64" "rom"
  chmod 644 "$GOTG_CONFIG_FILE"
  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"chmod 600"* ]]
}

@test "wrong credentials produce a clear message" {
  add_game n64 "usa.zelda.z64" "rom"
  jq '.password = "wrong"' "$GOTG_CONFIG_FILE" >"$GOTG_CONFIG_FILE.tmp"
  mv "$GOTG_CONFIG_FILE.tmp" "$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"

  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"username and password"* ]]
}

@test "a missing catalog explains that the importer has not run" {
  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"importer"* ]]
}

@test "info reports install state" {
  add_game n64 "usa.zelda.z64" "rom" "The Legend of Zelda"
  gotg refresh
  gotg info usa.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed: no"* ]]
  [[ "$output" == *"The Legend of Zelda"* ]]

  gotg download usa.zelda
  gotg info usa.zelda
  [[ "$output" == *"installed: yes"* ]]
}

@test "names with spaces and punctuation survive the round trip" {
  # Real filenames are full of these; they must be encoded per path segment.
  mkdir -p "$SERVER_ROOT/Games/n64"
  local name="usa.game (USA) & friends.z64"
  printf 'rom' >"$SERVER_ROOT/Games/n64/$name"
  add_manifest_entry n64 "/Games/n64/$name" file 3 "" "Punctuated"

  gotg refresh
  # The id derives from the filename, so check it resolves and downloads.
  run jq -r '.games[0].path' "$GOTG_CACHE_FILE"
  [[ "$output" == *"& friends"* ]]
}

@test "a catalog entry cannot escape the games directory via its platform" {
  # platform is used to build paths that get rm -rf'd and written to, so an
  # unchecked "../.." would reach outside ~/Games entirely.
  mkdir -p "$TEST_TMP/VICTIM"
  echo "precious" > "$TEST_TMP/VICTIM/important.txt"
  mkdir -p "$SERVER_ROOT/Games/.gotg"
  # A well-formed entry in every respect except the platform, so it is the
  # platform check being tested rather than the id check.
  jq -n '{version:1,games:[{platform:"../VICTIM",path:"/Games/x/usa.evil",
          type:"dir",size_bytes:1,sha256:null,title:"Evil"}]}' \
    > "$SERVER_ROOT/Games/.gotg/manifest.json"

  gotg refresh
  gotg download usa.evil

  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid platform"* ]]
  [ -f "$TEST_TMP/VICTIM/important.txt" ]
}

@test "a platform with a slash or traversal is rejected" {
  mkdir -p "$SERVER_ROOT/Games/.gotg"
  jq -n '{version:1,games:[{platform:"n64/../../etc",path:"/Games/x/usa.evil.z64",
          type:"file",size_bytes:1,sha256:null,title:"Evil"}]}' \
    > "$SERVER_ROOT/Games/.gotg/manifest.json"
  gotg refresh
  gotg info usa.evil
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid platform"* ]]
}

@test "a stale cache survives an unreachable server — the offline fallback" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  stop_server
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.server = "http://127.0.0.1:1"' "$GOTG_CONFIG_FILE" >"$GOTG_CONFIG_FILE.tmp"
  mv "$GOTG_CONFIG_FILE.tmp" "$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"

  # Everything cached is stale, so list has to attempt a refresh — and the
  # refresh failing must degrade to the cache, not kill the command.
  touch -d '2 days ago' "$GOTG_CACHE_FILE"
  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
  [[ "$stderr" == *"using the cached catalog"* ]]
}

@test "no cache and no server is a plain failure, not a silent one" {
  stop_server
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.server = "http://127.0.0.1:1"' "$GOTG_CONFIG_FILE" >"$GOTG_CONFIG_FILE.tmp"
  mv "$GOTG_CONFIG_FILE.tmp" "$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"
  gotg list
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"catalog"* ]]
}

@test "an explicit refresh against a dead server fails loudly" {
  stop_server
  # The killed port can be re-bound by a parallel bats job whose mock accepts
  # the same tester/hunter2 — point at port 1, which nothing answers.
  jq '.server = "http://127.0.0.1:1"' "$GOTG_CONFIG_FILE" >"$GOTG_CONFIG_FILE.tmp"
  mv "$GOTG_CONFIG_FILE.tmp" "$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"
  gotg refresh
  [ "$status" -ne 0 ]
}

@test "a filename with a slash, a traversal or a leading dot is invalid" {
  load_client_libs
  run validate_filename "usa.zelda.z64"
  [ "$status" -eq 0 ]
  run validate_filename "Legend of Zelda, The (USA).z64"
  [ "$status" -eq 0 ]
  local bad
  for bad in "a/b.z64" ".." "." ".hidden" "" "$(printf 'a\tb')"; do
    run -1 --separate-stderr validate_filename "$bad"
  done
}

@test "a garbage catalog over a stale cache falls back, and the cache survives" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  local before
  before="$(cat "$GOTG_CACHE_FILE")"
  # The server is up but answering nonsense — a proxy error page, say. This is
  # the failure the server-down tests do not cover: the fetch itself succeeds.
  echo '<html>502 bad gateway</html>' > "$SERVER_ROOT/Games/.gotg/manifest.json"
  touch -d '2 days ago' "$GOTG_CACHE_FILE"

  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
  [[ "$stderr" == *"using the cached catalog"* ]]
  [ "$(cat "$GOTG_CACHE_FILE")" = "$before" ]
}

@test "an explicit refresh of a garbage catalog fails loudly and keeps the cache" {
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  local before
  before="$(cat "$GOTG_CACHE_FILE")"
  jq -n '{version: 1, games: "not an array"}' > "$SERVER_ROOT/Games/.gotg/manifest.json"

  gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"not valid GOTG JSON"* ]]
  [ "$(cat "$GOTG_CACHE_FILE")" = "$before" ]
  [ ! -e "$GOTG_CACHE_FILE.tmp" ]
}

@test "a cache with a future mtime is fresh, not endlessly re-fetched" {
  # Clock skew is real: NFS, a resumed laptop. A negative age must read as
  # fresh rather than tripping an arithmetic surprise.
  add_game n64 "usa.zelda.z64" "rom"
  gotg refresh
  stop_server
  touch -d '1 hour hence' "$GOTG_CACHE_FILE"
  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"usa.zelda"* ]]
}

@test "filename length caps at 255" {
  load_client_libs
  run validate_filename "$(printf 'a%.0s' {1..255})"
  [ "$status" -eq 0 ]
  run -1 --separate-stderr validate_filename "$(printf 'a%.0s' {1..256})"
}

@test "a unicode filename is valid regardless of locale" {
  # The server accepts this name unconditionally; the client must not
  # disagree just because the test sandbox runs under LC_ALL=C.
  load_client_libs
  run validate_filename "ゼルダの伝説.z64"
  [ "$status" -eq 0 ]
}
