# shellcheck shell=bash
# Test harness: an isolated HOME plus the real GOTG service, so nothing here
# can reach the real cluster or touch the real ~/Games.

setup_env() {
  # Neither a terminal nor a display, which is how gotg runs under Steam and the
  # only way these can be deterministic: with a display inherited from whoever
  # ran bats, a download or a build waits on a zenity dialog nobody is watching.
  unset DISPLAY WAYLAND_DISPLAY

  export TEST_TMP="$BATS_TEST_TMPDIR"
  export GOTG_CONFIG_DIR="$TEST_TMP/config"
  export GOTG_STATE_DIR="$TEST_TMP/state"
  export GOTG_GAMES_DIR="$TEST_TMP/Games"
  export GOTG_CONFIG_FILE="$GOTG_CONFIG_DIR/config.json"
  export GOTG_CACHE_FILE="$GOTG_STATE_DIR/manifest.json"
  export GOTG_PARTIAL_DIR="$GOTG_GAMES_DIR/.gotg-partial"
  export GOTG_ROOTS_DIR="$GOTG_STATE_DIR/roots"
  export GOTG_ENV_STATE_DIR="$GOTG_STATE_DIR/env"
  export GOTG_SAVES_DIR="$GOTG_STATE_DIR/saves"
  export GOTG_APP_ROOT="$GOTG_STATE_DIR/app"
  export GOTG_LOG_DIR="$GOTG_STATE_DIR/logs"

  # Artwork has a source that needs no key, so it is reached on any `steam add`
  # — including from tests that are not about artwork at all. Pointed at a
  # closed port by default: refused instantly, and no test can quietly depend
  # on the internet. The ones that mean to override it.
  export GOTG_LIBRETRO_URL="http://127.0.0.1:1"

  mkdir -p "$GOTG_GAMES_DIR"
}

# A free port, so tests can run in parallel.
pick_port() {
  python3 - <<'EOF'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
EOF
}

# The GOTG service itself, real rather than mocked: the saves conflict rules
# live server-side now, and a stand-in would only ever test a copy of them.
_start_saves_service_once() {
  export SAVES_DATA_DIR="$TEST_TMP/service-saves"
  export SERVICE_FILES_DIR="$TEST_TMP/service-files"
  export SERVICE_LIBRARY_DIR="$TEST_TMP/library"
  mkdir -p "$SERVICE_FILES_DIR" "$SERVICE_LIBRARY_DIR" "$TEST_TMP/service-state"
  export SERVICE_PORT
  SERVICE_PORT="$(pick_port)"
  PYTHONUNBUFFERED=1 GOTG_PROXY_TOKEN="test-token" GOTG_SAVES_DIR="$SAVES_DATA_DIR" \
    GOTG_FILES_DIR="$SERVICE_FILES_DIR" \
    GOTG_LIBRARY_ROOTS="$SERVICE_LIBRARY_DIR" \
    GOTG_CATALOG_DB="$TEST_TMP/service-state/catalog.db" \
    GOTG_INDEX_TOKEN="index-token" \
    PORT="$SERVICE_PORT" \
    "${GOTG_SERVICE_BIN:-gotg-proxy}" >"$TEST_TMP/service.log" 2>&1 &
  export SERVICE_PID=$!
  export GOTG_SERVICE_URL="http://127.0.0.1:$SERVICE_PORT"

  local i
  for i in $(seq 1 50); do
    # A parallel job can win the port between pick_port and the bind; a dead
    # pid with a live healthz would be that other job's service.
    if ! kill -0 "$SERVICE_PID" 2>/dev/null; then
      return 1
    fi
    if curl -s -o /dev/null "$GOTG_SERVICE_URL/healthz" 2>/dev/null; then return 0; fi
    sleep 0.1
  done
  echo "the gotg service did not start" >&2
  return 1
}

start_saves_service() {
  local attempt
  for attempt in 1 2 3; do
    _start_saves_service_once && return 0
    stop_saves_service
  done
  echo "the gotg service did not start after 3 attempts" >&2
  return 1
}

stop_saves_service() {
  [[ -n "${SERVICE_PID:-}" ]] && kill "$SERVICE_PID" 2>/dev/null
  wait "${SERVICE_PID:-}" 2>/dev/null || true
}

# What `gotg saves setup` writes: the one config that carries both artwork
# and saves to the one service.
write_api_config() {
  mkdir -p "$GOTG_CONFIG_DIR"
  chmod 700 "$GOTG_CONFIG_DIR"
  jq -n --arg url "$GOTG_SERVICE_URL" --arg token "${1:-test-token}" \
    '{url: $url, token: $token}' >"$GOTG_CONFIG_DIR/api.json"
  chmod 600 "$GOTG_CONFIG_DIR/api.json"
}

# Publish a game into the real service's library and catalog, the way the
# indexer would: bytes in the library root, a row through the index token.
add_game() {
  local platform="$1" name="$2" content="$3" title="${4:-A Game}"
  local handler="${5:-single_file}"
  mkdir -p "$SERVICE_LIBRARY_DIR/$platform"
  printf '%s' "$content" >"$SERVICE_LIBRARY_DIR/$platform/$name"

  local file="$SERVICE_LIBRARY_DIR/$platform/$name"
  local sha size mtime id
  sha="$(sha256sum "$file" | cut -d' ' -f1)"
  size="$(stat -c '%s' "$file")"
  mtime="$(stat -c '%Y' "$file")"
  id="${name%.*}"

  jq -n --arg h "$handler" --arg t "$title" --arg n "$name" --arg p "$file" \
    --argjson s "$size" --argjson m "$mtime" --arg sha "$sha" \
    '{handler: $h, title: $t,
      files: [{name: $n, path: $p, size_bytes: $s, mtime: $m, sha256: $sha}]}' |
    curl -gfsS -X PUT -H "Authorization: Bearer index-token" \
      --data-binary @- "$GOTG_SERVICE_URL/catalog/$platform/$id" >/dev/null
}

# The old manifest-shaped seeding, kept for the suites that only need a row
# to exist: the entry lands in the service catalog, bytes optional.
add_manifest_entry() {
  local platform="$1" path="$2" type="$3" size="$4" sha="$5" title="$6"
  local name id
  name="$(basename "$path")"
  id="${name%.*}"
  [[ "$type" == "dir" ]] && id="$name"
  jq -n --arg t "$title" --arg n "$name" \
    --arg p "$SERVICE_LIBRARY_DIR/$platform/$name" \
    --argjson s "$size" --arg sha "$sha" \
    '{handler: "single_file", title: $t,
      files: [{name: $n, path: $p, size_bytes: $s, mtime: 1,
               sha256: (if $sha == "" then null else $sha end)}]}' |
    curl -gfsS -X PUT -H "Authorization: Bearer index-token" \
      --data-binary @- "$GOTG_SERVICE_URL/catalog/$platform/$id" >/dev/null
}

# One member added to an existing entry (or a fresh multi-member entry): the
# scene sets and trees. Callers build the entry JSON themselves when the shape
# matters; this covers the common cases.
add_member_game() {
  local platform="$1" id="$2" title="$3" handler="$4" files_json="$5"
  jq -n --arg h "$handler" --arg t "$title" --argjson f "$files_json" \
    '{handler: $h, title: $t, files: $f}' |
    curl -gfsS -X PUT -H "Authorization: Bearer index-token" \
      --data-binary @- "$GOTG_SERVICE_URL/catalog/$platform/$id" >/dev/null
}

gotg() {
  run --separate-stderr "$GOTG_BIN" "$@"
}

# Source the client's own libraries into the test shell.
#
# The blob layer has no command of its own, and reaching it through `gotg saves`
# would confound a backend fault with a conflict-model one — so these are called
# directly. Set up exactly as bin/gotg sets them up, from the installed package
# rather than the checkout, so what is tested is what is shipped.
load_client_libs() {
  local root
  root="$(cd "$(dirname "$GOTG_BIN")/../share/gotg" && pwd)"
  export GOTG_ROOT="$root"
  export GOTG_LIB="$root/lib"
  export GOTG_DATA="${GOTG_DATA:-$root/data}"
  export GOTG_TEMPLATES="$root/templates"
  export GOTG_ENV_DIR="${GOTG_ENV_DIR:-$root/env}"

  # shellcheck source=/dev/null
  local lib
  for lib in color common config manifest download env launcher pads pads-dolphin pads-ryujinx keys firmware remote saves; do
    source "$GOTG_LIB/$lib.sh"
  done
}

# A stand-in for a built environment: the GC root that `gotg play` execs, with no
# nix involved. Absolute shebang, because /usr/bin/env does not exist inside the
# nix build sandbox these run in.
fake_env() {
  local attr="$1"
  # A second word in the same `local` would expand $attr before it is assigned.
  local dir="$GOTG_ROOTS_DIR/$attr"
  local saves="${2:-[]}" excludes="${3:-[]}"
  mkdir -p "$dir/bin" "$dir/share/gotg"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'echo "%s launched with: $*"\n' "$attr"
  } >"$dir/bin/gotg-play"
  chmod +x "$dir/bin/gotg-play"

  # The real derivation emits this beside the runnable; the saves commands read
  # it from the GC root rather than evaluating nix.
  jq -n --arg name "$attr" --argjson saves "$saves" --argjson excludes "$excludes" \
    '{version: 1, name: $name, saves: $saves, excludes: $excludes,
      legacy: [], saveStates: false}' \
    >"$dir/share/gotg/saves.json"
}

# The recipe a built harkinian env carries, on a root fake_env already made:
# unpack the zipped ROM into the bare destination directory.
stub_unzip_recipe() {
  local root="$GOTG_ROOTS_DIR/$1"
  {
    printf '#!%s\n' "$(command -v bash)"
    cat <<'SHIM'
handler="$1"; raw="$2"; dest="$3"
rm -rf "$dest"; mkdir -p "$dest"
unzip -q "$raw"/*.zip -d "$dest"
SHIM
  } >"$root/bin/gotg-recipe"
  chmod +x "$root/bin/gotg-recipe"
  jq -n '{handlers: ["single_file"]}' >"$root/share/gotg/recipe.json"
}

# A stand-in `nix`, so that building an environment can be tested where there is
# no nix to run. It records what it was asked to build and then produces the GC
# root, or — with "fail" — records it and gives up, as a build of a broken
# environment would.
stub_nix() {
  local mode="${1:-ok}"
  export NIX_LOG="$TEST_TMP/nix.log"
  export GOTG_FLAKE="$TEST_TMP/flake"
  export GOTG_NIX="$TEST_TMP/bin/nix"
  export SHIM_BASH
  SHIM_BASH="$(command -v bash)"
  mkdir -p "$GOTG_FLAKE" "$TEST_TMP/bin"
  : >"$GOTG_FLAKE/flake.nix"

  {
    printf '#!%s\n' "$SHIM_BASH"
    cat <<'SHIM'
printf '%s\n' "$*" >>"$NIX_LOG"
SHIM
    if [[ "$mode" == "fail" ]]; then printf 'exit 1\n'; fi
    cat <<'SHIM'
out=""
prev=""
for arg in "$@"; do
  if [[ "$prev" == "-o" ]]; then out="$arg"; fi
  prev="$arg"
done
[[ -n "$out" ]] || exit 1
mkdir -p "$out/bin"
{
  printf '#!%s\n' "$SHIM_BASH"
  printf 'echo "built launched with: $*"\n'
} >"$out/bin/gotg-play"
chmod +x "$out/bin/gotg-play"
exit 0
SHIM
  } >"$GOTG_NIX"
  chmod +x "$GOTG_NIX"
}
