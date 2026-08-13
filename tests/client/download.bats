#!/usr/bin/env bats
# Downloading: the path a Steam launch takes when a game is not here yet.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  start_saves_service
  write_api_config
}

teardown() {
  stop_saves_service
}

@test "the token travels in a header, never a query parameter" {
  add_game n64 "usa.zelda.z64" "rom-content"
  local url="$GOTG_SERVICE_URL/games/n64/usa.zelda/usa.zelda.z64"

  run curl -s -o /dev/null -w '%{http_code}' "$url?token=test-token"
  [ "$output" = "401" ]

  run curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer test-token' "$url"
  [ "$output" = "200" ]
}

@test "list marks which games are already installed" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  gotg refresh
  gotg list
  [ "$status" -eq 0 ]
  [[ "$output" == *"[ ]"*"usa.zelda"* ]]

  gotg download usa.zelda
  [ "$status" -eq 0 ]
  gotg list
  [[ "$output" == *"[*]"*"usa.zelda"* ]]
}

@test "a downloaded game matches the service byte for byte" {
  add_game n64 "usa.zelda.z64" "the actual rom bytes"
  gotg refresh
  gotg download usa.zelda

  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
  run diff "$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64" "$GOTG_GAMES_DIR/n64/usa.zelda.z64"
  [ "$status" -eq 0 ]
}

@test "a corrupted download is rejected and deleted, not installed" {
  # A row whose hash cannot match, as a truncated transfer would produce. The
  # service serves the bytes happily; the client's verification is the gate.
  local file="$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64"
  mkdir -p "$SERVICE_LIBRARY_DIR/n64"
  printf 'rom-content' >"$file"
  jq -n --arg p "$file" \
    '{handler: "single_file", title: "Zelda",
      files: [{name: "usa.zelda.z64", path: $p, size_bytes: 11, mtime: 1,
               sha256: ("0" * 64)}]}' |
    curl -fsS -X PUT -H "Authorization: Bearer index-token" \
      --data-binary @- "$GOTG_SERVICE_URL/catalog/n64/usa.zelda" >/dev/null

  gotg refresh
  gotg download usa.zelda

  [ "$status" -ne 0 ]
  [[ "$stderr" == *"checksum mismatch"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}

@test "an unzip game installs as its unpacked tree for both catalog handlers" {
  # The No-Intro importer stamps no_intro_set on the wire (plan.py maps the
  # N64 DAT dir to it); the games-root pass stamps single_file. The unzip
  # override must route both through the environment's recipe.
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/n64"

  local handler id attr
  for handler in single_file no_intro_set; do
    id="usa.zelda_$handler"
    attr="env-n64-${id/./_}"
    publish_unzip_game "$id" "$handler"
    : >"$GOTG_ENV_DIR/games/n64/$id.nix"
    fake_env "$attr"
    stub_unzip_recipe "$attr"

    gotg refresh
    gotg download "$id"
    [ "$status" -eq 0 ]

    [ -d "$GOTG_GAMES_DIR/n64/$id" ]
    [ -f "$GOTG_GAMES_DIR/n64/$id/Zelda (USA).z64" ]
    [ ! -e "$GOTG_GAMES_DIR/n64/$id.zip" ]
    [ ! -e "$GOTG_PARTIAL_DIR/$id" ]

    # The bare id directory is what game_installed_path reads for this shape.
    gotg download "$id"
    [ "$status" -eq 0 ]
    [[ "$stderr" == *"already installed"* ]]
  done
}

@test "an unzip game whose environment declares a different handler set is refused" {
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR/games/n64"
  publish_unzip_game usa.zeldac no_intro_set
  : >"$GOTG_ENV_DIR/games/n64/usa.zeldac.nix"
  fake_env env-n64-usa_zeldac
  stub_unzip_recipe env-n64-usa_zeldac
  jq -n '{handlers: ["scene_archive"]}' \
    >"$GOTG_ROOTS_DIR/env-n64-usa_zeldac/share/gotg/recipe.json"

  gotg refresh
  gotg download usa.zeldac
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"declares no recipe for 'no_intro_set'"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zeldac" ]
  # The transfer is the expensive half; the refusal must keep the raw members.
  [ -f "$GOTG_PARTIAL_DIR/usa.zeldac/usa.zeldac.zip" ]
}

@test "an unzip game without its environment built says how to fix it" {
  # GOTG_ENV_DIR stays the shipped tree on purpose: env_attr resolves the real
  # n64.nix to env-n64, whose root was never built here.
  publish_unzip_game usa.zeldab single_file

  gotg refresh
  gotg download usa.zeldab
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"gotg install usa.zeldab"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zeldab" ]
}

@test "an interrupted download resumes instead of starting over" {
  local payload
  payload="$(head -c 200000 /dev/urandom | base64 | head -c 100000)"
  add_game n64 "usa.big.z64" "$payload"
  gotg refresh

  # A previous attempt left the first 20k in staging; the next one must
  # continue from there, not restart — and the result must be byte-exact.
  mkdir -p "$GOTG_PARTIAL_DIR/usa.big"
  head -c 20000 "$SERVICE_LIBRARY_DIR/n64/usa.big.z64" \
    >"$GOTG_PARTIAL_DIR/usa.big/usa.big.z64"

  gotg download usa.big
  [ "$status" -eq 0 ]
  run diff "$SERVICE_LIBRARY_DIR/n64/usa.big.z64" "$GOTG_GAMES_DIR/n64/usa.big.z64"
  [ "$status" -eq 0 ]
}

@test "a multi-member entry arrives as its tree" {
  mkdir -p "$SERVICE_LIBRARY_DIR/wiiu/dump/code" "$SERVICE_LIBRARY_DIR/wiiu/dump/meta"
  echo rpx >"$SERVICE_LIBRARY_DIR/wiiu/dump/code/game.rpx"
  echo xml >"$SERVICE_LIBRARY_DIR/wiiu/dump/meta/meta.xml"
  local files
  files="$(jq -n --arg root "$SERVICE_LIBRARY_DIR/wiiu/dump" '[
    {name: "code/game.rpx", path: ($root + "/code/game.rpx"), size_bytes: 4, mtime: 1, sha256: null},
    {name: "meta/meta.xml", path: ($root + "/meta/meta.xml"), size_bytes: 4, mtime: 1, sha256: null}
  ]')"
  add_member_game wiiu usa.title "A Wii U Game" wiiu_decrypted "$files"

  gotg refresh
  gotg download usa.title

  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/wiiu/usa.title/code/game.rpx" ]
  [ -f "$GOTG_GAMES_DIR/wiiu/usa.title/meta/meta.xml" ]
}

@test "downloading twice does not re-fetch" {
  add_game n64 "usa.zelda.z64" "rom-content"
  gotg refresh
  gotg download usa.zelda
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already installed"* ]]
}

@test "a failed download never looks like an installed game" {
  add_game n64 "usa.zelda.z64" "rom-content"
  gotg refresh
  # The service goes away mid-life; the download must fail without leaving
  # anything where a launcher would look.
  stop_saves_service
  jq '.url = "http://127.0.0.1:1"' "$GOTG_CONFIG_DIR/api.json" >"$GOTG_CONFIG_DIR/api.json.tmp"
  mv "$GOTG_CONFIG_DIR/api.json.tmp" "$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"

  gotg download usa.zelda
  [ "$status" -ne 0 ]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
  start_saves_service
}

@test "a recipe handler without a built environment says how to fix it" {
  local files
  mkdir -p "$SERVICE_LIBRARY_DIR/switch/rel"
  printf 'rar' >"$SERVICE_LIBRARY_DIR/switch/rel/g.rar"
  files="$(jq -n --arg p "$SERVICE_LIBRARY_DIR/switch/rel/g.rar" \
    '[{name: "g.rar", path: $p, size_bytes: 3, mtime: 1, sha256: null}]')"
  add_member_game switch world.game "A Game" scene_archive "$files"

  gotg refresh
  gotg download world.game
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"recipe"* ]]
}

# --- the recipe install path -------------------------------------------------

# A built environment that carries a recipe, with no nix involved: the GC root
# gotg-play stub from fake_env, plus the two files _run_recipe reads.
stub_recipe_env() {
  local platform="$1" mode="${2:-ok}"
  export GOTG_ENV_DIR="$TEST_TMP/env"
  mkdir -p "$GOTG_ENV_DIR"
  : >"$GOTG_ENV_DIR/$platform.nix"
  fake_env "env-$platform"

  local root="$GOTG_ROOTS_DIR/env-$platform"
  {
    printf '#!%s\n' "$(command -v bash)"
    if [[ "$mode" == "fail" ]]; then
      printf 'echo "gotg-recipe: boom" >&2\nexit 1\n'
    else
      cat <<'SHIM'
handler="$1"; raw="$2"; dest="$3"
printf 'refined:%s' "$(cat "$raw"/*.rar)" >"$dest.xci"
SHIM
    fi
  } >"$root/bin/gotg-recipe"
  chmod +x "$root/bin/gotg-recipe"
  jq -n '{handlers: ["scene_archive"]}' >"$root/share/gotg/recipe.json"
}

publish_scene_game() {
  mkdir -p "$SERVICE_LIBRARY_DIR/switch"
  printf 'raw-rar-bytes' >"$SERVICE_LIBRARY_DIR/switch/world.game.rar"
  local f="$SERVICE_LIBRARY_DIR/switch/world.game.rar" sha files
  sha="$(sha256sum "$f" | cut -d' ' -f1)"
  files="$(jq -n --arg p "$f" --arg sha "$sha" \
    '[{name: "world.game.rar", path: $p, size_bytes: 13, mtime: 1, sha256: $sha}]')"
  add_member_game switch world.game "A Game" scene_archive "$files"
}

@test "a recipe refines the raw member into id.<ext> and cleans staging" {
  publish_scene_game
  stub_recipe_env switch

  gotg refresh
  gotg download world.game
  [ "$status" -eq 0 ]

  [ -f "$GOTG_GAMES_DIR/switch/world.game.xci" ]
  run cat "$GOTG_GAMES_DIR/switch/world.game.xci"
  [ "$output" = "refined:raw-rar-bytes" ]
  # The raw members are re-downloadable and the artifact is deterministic, so
  # staging must not survive the install.
  [ ! -e "$GOTG_PARTIAL_DIR/world.game" ]

  gotg download world.game
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already installed"* ]]
  gotg list
  [[ "$output" == *"[*]"*"world.game"* ]]
}

@test "a failed recipe keeps the raw members and installs nothing" {
  publish_scene_game
  stub_recipe_env switch fail

  gotg refresh
  gotg download world.game
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"recipe failed"* ]]
  [ ! -e "$GOTG_GAMES_DIR/switch/world.game.xci" ]
  # The transfer is the expensive half; a broken recipe must not cost it.
  [ -f "$GOTG_PARTIAL_DIR/world.game/world.game.rar" ]

  stub_recipe_env switch
  gotg download world.game
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/switch/world.game.xci" ]
  [ ! -e "$GOTG_PARTIAL_DIR/world.game" ]
}

@test "an environment that does not declare the handler is refused" {
  publish_scene_game
  stub_recipe_env switch
  jq -n '{handlers: ["other_thing"]}' \
    >"$GOTG_ROOTS_DIR/env-switch/share/gotg/recipe.json"

  gotg refresh
  gotg download world.game
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"declares no recipe for 'scene_archive'"* ]]
  [ ! -e "$GOTG_GAMES_DIR/switch/world.game.xci" ]
}

@test "a poisoned cache row with a malformed sha never reaches the network" {
  mkdir -p "$GOTG_STATE_DIR"
  jq -n '{version: 2, games: [{id: "usa.bad", platform: "n64", handler: "single_file",
    title: "T", files: [{name: "usa.bad.z64", size_bytes: 3, sha256: "deadbeef"}]}]}' \
    >"$GOTG_CACHE_FILE"

  gotg download usa.bad
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"invalid sha256"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.bad.z64" ]
}

@test "a complete but corrupt staged member is caught, deleted, and refetched" {
  add_game n64 "usa.zelda.z64" "the real bytes"
  gotg refresh

  mkdir -p "$GOTG_PARTIAL_DIR/usa.zelda"
  printf 'wrong bytes!!!' >"$GOTG_PARTIAL_DIR/usa.zelda/usa.zelda.z64"

  gotg download usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"checksum mismatch"* ]]
  [ ! -e "$GOTG_PARTIAL_DIR/usa.zelda/usa.zelda.z64" ]

  gotg download usa.zelda
  [ "$status" -eq 0 ]
  run diff "$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64" "$GOTG_GAMES_DIR/n64/usa.zelda.z64"
  [ "$status" -eq 0 ]
}

@test "nested member names with spaces and parentheses survive the trip" {
  mkdir -p "$SERVICE_LIBRARY_DIR/wiiu/dump/content/My Game (USA) [!]"
  printf 'inner bytes' >"$SERVICE_LIBRARY_DIR/wiiu/dump/content/My Game (USA) [!]/data file.bin"
  printf 'xml' >"$SERVICE_LIBRARY_DIR/wiiu/dump/meta.xml"
  local files
  files="$(jq -n --arg root "$SERVICE_LIBRARY_DIR/wiiu/dump" '[
    {name: "content/My Game (USA) [!]/data file.bin",
     path: ($root + "/content/My Game (USA) [!]/data file.bin"),
     size_bytes: 11, mtime: 1, sha256: null},
    {name: "meta.xml", path: ($root + "/meta.xml"), size_bytes: 3, mtime: 1, sha256: null}
  ]')"
  add_member_game wiiu usa.title "A Wii U Game" wiiu_decrypted "$files"

  gotg refresh
  gotg download usa.title
  [ "$status" -eq 0 ]

  run diff "$SERVICE_LIBRARY_DIR/wiiu/dump/content/My Game (USA) [!]/data file.bin" \
    "$GOTG_GAMES_DIR/wiiu/usa.title/content/My Game (USA) [!]/data file.bin"
  [ "$status" -eq 0 ]
  # Encoded on the wire, decoded on disk: no percent-escapes may leak into paths.
  [ ! -e "$GOTG_GAMES_DIR/wiiu/usa.title/content/My%20Game%20(USA)%20%5B!%5D" ]
}

@test "the catalog's files_url is where the bytes are fetched from" {
  add_game n64 "usa.zelda.z64" "rom-content"
  gotg refresh
  # The service in this suite names no files_url; planting one that points at
  # the same host proves the client honors it when present.
  jq --arg u "$GOTG_SERVICE_URL" '. + {files_url: $u}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}

@test "a files_url that stopped answering costs a catalog refresh, not the download" {
  # The byte host is the catalog's to name and can move between reads — the
  # refresh in the retry path must pick up the current one.
  add_game n64 "usa.zelda.z64" "rom-content"
  gotg refresh
  jq '. + {files_url: "http://127.0.0.1:1"}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}

@test "a files_url that is not a url is ignored rather than fetched" {
  add_game n64 "usa.zelda.z64" "rom-content"
  gotg refresh
  jq '. + {files_url: "ftp://evil.example"}' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp"
  mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}
