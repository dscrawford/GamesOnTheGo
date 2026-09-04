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

@test "a complete but corrupt staged member heals in one run" {
  # This used to take two runs — die on the mismatch, refetch on the next
  # try. The retry ladder folds the second run in: mismatch, delete, fresh
  # fetch, verified, installed.
  add_game n64 "usa.zelda.z64" "the real bytes"
  gotg refresh

  mkdir -p "$GOTG_PARTIAL_DIR/usa.zelda"
  printf 'wrong bytes!!!' >"$GOTG_PARTIAL_DIR/usa.zelda/usa.zelda.z64"

  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"checksum mismatch"* ]]
  [[ "$stderr" == *"retrying"* ]]
  run diff "$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64" "$GOTG_GAMES_DIR/n64/usa.zelda.z64"
  [ "$status" -eq 0 ]
}

@test "bytes that can never verify still die after the retries run out" {
  # The server itself serving wrong bytes must not loop or install: three
  # attempts, then a refusal that keeps nothing.
  local file="$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64"
  mkdir -p "$SERVICE_LIBRARY_DIR/n64"
  printf 'rom-content' >"$file"
  jq -n --arg p "$file" \
    '{handler: "single_file", title: "Zelda",
      files: [{name: "usa.zelda.z64", path: $p, size_bytes: 11,
               mtime: 1, sha256: ("ab" * 32)}]}' |
    curl -sS -X PUT -H "Authorization: Bearer index-token" --data-binary @- \
      "$GOTG_SERVICE_URL/catalog/n64/usa.zelda" >/dev/null
  gotg refresh
  gotg download usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"download failed"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
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

@test "an interrupted partial resumes instead of restarting from zero" {
  add_game n64 "usa.zelda.z64" "the full rom content for resume"
  gotg refresh
  # Half the bytes already here, as a reset mid-transfer leaves them.
  mkdir -p "$GOTG_PARTIAL_DIR/usa.zelda"
  printf 'the full rom con' >"$GOTG_PARTIAL_DIR/usa.zelda/usa.zelda.z64"
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  run diff "$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64" "$GOTG_GAMES_DIR/n64/usa.zelda.z64"
  [ "$status" -eq 0 ]
}

@test "a partial the server cannot resume is dropped and refetched" {
  add_game n64 "usa.zelda.z64" "short"
  gotg refresh
  # Longer than the remote file: a resume offset past the end, which the
  # service answers with the whole file and curl refuses as exit 33.
  mkdir -p "$GOTG_PARTIAL_DIR/usa.zelda"
  printf 'this partial is much longer than the real file' \
    >"$GOTG_PARTIAL_DIR/usa.zelda/usa.zelda.z64"
  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ "$(cat "$GOTG_GAMES_DIR/n64/usa.zelda.z64")" = "short" ]
}

# --- updates and DLC ----------------------------------------------------------

# A game whose entry carries an update (a rar set) and a DLC (a loose nsp)
# under extras/, on a single_file base: the shape a Switch title takes once
# the importer attaches what arrived after it.
publish_bundle_game() {
  local dir="$SERVICE_LIBRARY_DIR/switch"
  mkdir -p "$dir/Zelda_Update_v1.4.3" "$dir/dlc"
  printf 'base-bytes' >"$dir/world.zelda.nsp"
  printf 'update-rar' >"$dir/Zelda_Update_v1.4.3/u.rar"
  printf 'dlc-bytes' >"$dir/dlc/pack.nsp"
  local files
  files="$(jq -n --arg d "$dir" \
    --arg s1 "$(sha256sum "$dir/world.zelda.nsp" | cut -d' ' -f1)" \
    --arg s2 "$(sha256sum "$dir/Zelda_Update_v1.4.3/u.rar" | cut -d' ' -f1)" \
    --arg s3 "$(sha256sum "$dir/dlc/pack.nsp" | cut -d' ' -f1)" \
    '[{name: "world.zelda.nsp", path: ($d + "/world.zelda.nsp"), size_bytes: 10, mtime: 1, sha256: $s1},
      {name: "extras/update_1.4.3/u.rar", path: ($d + "/Zelda_Update_v1.4.3/u.rar"), size_bytes: 10, mtime: 1, sha256: $s2},
      {name: "extras/dlc_pack/pack.nsp", path: ($d + "/dlc/pack.nsp"), size_bytes: 9, mtime: 1, sha256: $s3}]')"
  add_member_game switch world.zelda "Zelda" single_file "$files"
}

# The switch recipe's shape, without nix: <id>/<id>.<ext> plus extras/, built
# from whatever staged — which is what the assertions below read back.
stub_bundle_recipe_env() {
  stub_recipe_env switch
  {
    printf '#!%s\n' "$(command -v bash)"
    cat <<'SHIM'
handler="$1"; raw="$2"; dest="$3"
mkdir -p "$dest/extras"
cp "$raw"/world.zelda.nsp "$dest/world.zelda.nsp"
for release in "$raw"/extras/*/; do
  name="$(basename "$release")"
  for f in "$release"/*; do cp "$f" "$dest/extras/$name-$(basename "$f")"; done
done
printf '%s' "$handler" >"$dest/handler"
SHIM
  } >"$GOTG_ROOTS_DIR/env-switch/bin/gotg-recipe"
  chmod +x "$GOTG_ROOTS_DIR/env-switch/bin/gotg-recipe"
  jq -n '{handlers: ["single_file"]}' >"$GOTG_ROOTS_DIR/env-switch/share/gotg/recipe.json"
}

@test "attached extras stage under their release directories and install as a bundle" {
  publish_bundle_game
  stub_bundle_recipe_env
  gotg refresh

  gotg download world.zelda
  [ "$status" -eq 0 ]

  local install="$GOTG_GAMES_DIR/switch/world.zelda"
  [ -d "$install" ]
  [ "$(cat "$install/world.zelda.nsp")" = base-bytes ]
  [ "$(cat "$install/extras/update_1.4.3-u.rar")" = update-rar ]
  [ "$(cat "$install/extras/dlc_pack-pack.nsp")" = dlc-bytes ]
  # A single_file base with extras still goes through the recipe, as itself.
  [ "$(cat "$install/handler")" = single_file ]
  [ ! -e "$GOTG_GAMES_DIR/switch/world.zelda.nsp" ]
  [ ! -e "$GOTG_PARTIAL_DIR/world.zelda" ]

  gotg list --installed
  [[ "$output" == *"[*]"*"world.zelda"* ]]
  gotg complete installed
  [[ "$output" == *"switch/world.zelda"* ]]
}

@test "a bundle launches its game file, not its directory" {
  publish_bundle_game
  stub_bundle_recipe_env
  gotg refresh
  gotg download world.zelda
  [ "$status" -eq 0 ]

  gotg play world.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-switch launched with: $GOTG_GAMES_DIR/switch/world.zelda/world.zelda.nsp"* ]]
}

@test "uninstalling a bundle removes the directory" {
  publish_bundle_game
  stub_bundle_recipe_env
  gotg refresh
  gotg download world.zelda
  [ "$status" -eq 0 ]

  gotg uninstall world.zelda
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_GAMES_DIR/switch/world.zelda" ]
}

@test "a game installed before its extras arrived is still the game, and says so" {
  add_game switch "world.zelda.nsp" "base-bytes" "Zelda"
  gotg refresh
  gotg download world.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/switch/world.zelda.nsp" ]

  # The catalog row gains an update; the file on disk predates it.
  publish_bundle_game
  stub_bundle_recipe_env
  gotg refresh

  gotg list --installed
  [[ "$output" == *"[*]"*"world.zelda"* ]]
  gotg download world.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"without its updates and DLC"* ]]
  [ ! -d "$GOTG_GAMES_DIR/switch/world.zelda" ]
  gotg play world.zelda
  [ "$status" -eq 0 ]
  [[ "$output" == *"env-switch launched with: $GOTG_GAMES_DIR/switch/world.zelda.nsp"* ]]

  gotg uninstall world.zelda
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_GAMES_DIR/switch/world.zelda.nsp" ]
  gotg download world.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/switch/world.zelda/extras/dlc_pack-pack.nsp" ]
}

@test "GOTG_FILES_URL names where the bytes come from, over the catalog's own host" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  gotg refresh
  # The catalog's byte host is unreachable; the override is the service itself.
  jq '.files_url = "http://127.0.0.1:9/"' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp" && mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  GOTG_FILES_URL="$GOTG_SERVICE_URL/" gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
  # And a value that is not a url is ignored rather than dialled: the
  # catalog's host is used, which the retry's refresh puts back.
  rm "$GOTG_GAMES_DIR/n64/usa.zelda.z64"
  GOTG_FILES_URL="not-a-url" gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" != *"not-a-url"* ]]
}

# --- the library, mounted -----------------------------------------------------

@test "with the library mounted, members are copied from it and still verified" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  # The library token sees server paths; a mount at the library's own path
  # is where the copy comes from. The byte host is made unreachable to prove
  # nothing was streamed.
  write_api_config library-token
  GOTG_LIBRARY_MOUNT="$SERVICE_LIBRARY_DIR" gotg refresh
  [ "$status" -eq 0 ]
  run jq -r '.games[0].files[0].path' "$GOTG_CACHE_FILE"
  [ "$output" = "$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64" ]
  jq '.files_url = "http://127.0.0.1:9/"' "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp" && mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"

  GOTG_FILES_URL="http://127.0.0.1:9" GOTG_LIBRARY_MOUNT="$SERVICE_LIBRARY_DIR" gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"copying usa.zelda.z64 from the library"* ]]
  [ "$(cat "$GOTG_GAMES_DIR/n64/usa.zelda.z64")" = rom-content ]

  # A library copy that does not match the catalog is refused, not installed.
  gotg uninstall usa.zelda
  printf 'tampered' >"$SERVICE_LIBRARY_DIR/n64/usa.zelda.z64"
  GOTG_FILES_URL="http://127.0.0.1:9" GOTG_LIBRARY_MOUNT="$SERVICE_LIBRARY_DIR" gotg download usa.zelda
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"does not match the catalog"* ]]
  [ ! -e "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}

@test "a client token gets no paths, and a path outside the mount is streamed" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  GOTG_LIBRARY_MOUNT="$SERVICE_LIBRARY_DIR" gotg refresh
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"403"* ]]

  write_api_config library-token
  GOTG_LIBRARY_MOUNT="$TEST_TMP/elsewhere" gotg refresh
  [ "$status" -eq 0 ]
  GOTG_LIBRARY_MOUNT="$TEST_TMP/elsewhere" gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" != *"copying"* ]]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
}

@test "a cache from before the library was mounted is refreshed for its paths" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  write_api_config library-token
  gotg refresh
  run jq -r '.games[0].files[0].path // "none"' "$GOTG_CACHE_FILE"
  [ "$output" = none ]

  GOTG_FILES_URL="http://127.0.0.1:9" GOTG_LIBRARY_MOUNT="$SERVICE_LIBRARY_DIR" gotg download usa.zelda
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"catalog updated"* ]]
  [[ "$stderr" == *"copying usa.zelda.z64 from the library"* ]]
  [ "$(cat "$GOTG_GAMES_DIR/n64/usa.zelda.z64")" = rom-content ]
}

# --- more than one byte host ----------------------------------------------------

@test "the first byte host that answers is used, the others left alone" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  gotg refresh
  # The catalog prefers a host this machine cannot reach — a tailnet address
  # from outside the tailnet — with the service itself behind it.
  jq --arg s "$GOTG_SERVICE_URL" '.files_urls = ["http://127.0.0.1:9", $s] | .files_url = $s' \
    "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp" && mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"

  gotg download usa.zelda
  [ "$status" -eq 0 ]
  [ -f "$GOTG_GAMES_DIR/n64/usa.zelda.z64" ]
  [[ "$stderr" != *"retrying"* ]]
}

@test "with no host answering, the first is tried and its failure is what is reported" {
  add_game n64 "usa.zelda.z64" "rom-content" "Zelda"
  gotg refresh
  load_client_libs
  jq '.files_urls = ["http://127.0.0.1:9", "http://127.0.0.1:10"] | .files_url = "http://127.0.0.1:10"' \
    "$GOTG_CACHE_FILE" >"$GOTG_CACHE_FILE.tmp" && mv "$GOTG_CACHE_FILE.tmp" "$GOTG_CACHE_FILE"
  run manifest_files_hosts
  [ "${lines[0]}" = "http://127.0.0.1:9" ]
  [ "${lines[1]}" = "http://127.0.0.1:10" ]
  [ "${#lines[@]}" -eq 2 ]
  run manifest_files_pick
  [ "$output" = "http://127.0.0.1:9" ]
  # And the override names one host only, whatever the catalog says.
  GOTG_FILES_URL="$GOTG_SERVICE_URL/" run manifest_files_hosts
  [ "$output" = "$GOTG_SERVICE_URL" ]
}

# --- the meter ------------------------------------------------------------------

@test "the meter says how far, how fast and how long to go" {
  load_client_libs
  # 300 MB of 1 GB after 30 s from nothing: 10 MB/s, ~72 s left.
  run _meter_line $((300 * 1048576)) $((1024 * 1048576)) 0 1000 1030
  [ "$output" = " 29%  300.0 MB of 1.0 GB  10.0 MB/s  eta 1m12s" ]
  # Resumed bytes count toward progress, not toward speed.
  run _meter_line $((300 * 1048576)) $((1024 * 1048576)) $((200 * 1048576)) 1000 1010
  [[ "$output" == " 29%  300.0 MB of 1.0 GB  10.0 MB/s  eta "* ]]
  # No size known: bytes and speed only. No time passed: no speed, no eta.
  run _meter_line 5242880 0 0 1000 1005
  [ "$output" = "5.0 MB  1.0 MB/s" ]
  run _meter_line 5242880 10485760 0 1000 1000
  [ "$output" = " 50%  5.0 MB of 10.0 MB" ]
  # Hours read as hours.
  run _meter_line 1048576 $((100 * 1048576 * 1024)) 0 1000 1001
  [[ "$output" == *"eta "*h*m ]]
}
