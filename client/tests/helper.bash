# shellcheck shell=bash
# Test harness: an isolated HOME plus a mock File Browser, so nothing here can
# reach the real server or touch the real ~/Games.

setup_env() {
  # Neither a terminal nor a display, which is how gotg runs under Steam and the
  # only way these can be deterministic: with a display inherited from whoever
  # ran bats, a download or a build waits on a zenity dialog nobody is watching.
  unset DISPLAY WAYLAND_DISPLAY

  export TEST_TMP="$BATS_TEST_TMPDIR"
  export SERVER_ROOT="$TEST_TMP/server"
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
  mkdir -p "$SERVER_ROOT/Games/.gotg" "$GOTG_GAMES_DIR"
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

start_server() {
  export MOCK_PORT
  MOCK_PORT="$(pick_port)"
  python3 "$BATS_TEST_DIRNAME/mock_filebrowser.py" "$SERVER_ROOT" "$MOCK_PORT" "$@" &
  export MOCK_PID=$!
  export GOTG_SERVER_URL="http://127.0.0.1:$MOCK_PORT"

  local i
  for i in $(seq 1 50); do
    if curl -s -o /dev/null "$GOTG_SERVER_URL/api/raw/nope" 2>/dev/null; then return 0; fi
    sleep 0.1
  done
  echo "mock server did not start" >&2
  return 1
}

stop_server() {
  [[ -n "${MOCK_PID:-}" ]] && kill "$MOCK_PID" 2>/dev/null
  wait "${MOCK_PID:-}" 2>/dev/null || true
}

write_config() {
  mkdir -p "$GOTG_CONFIG_DIR"
  jq -n --arg s "$GOTG_SERVER_URL" \
    '{server: $s, username: "tester", password: "hunter2", remote_root: "/Games"}' \
    >"$GOTG_CONFIG_FILE"
  chmod 600 "$GOTG_CONFIG_FILE"
}

# Publish a game on the mock server and add it to the catalog it serves.
add_game() {
  local platform="$1" name="$2" content="$3" title="${4:-A Game}"
  mkdir -p "$SERVER_ROOT/Games/$platform"
  printf '%s' "$content" >"$SERVER_ROOT/Games/$platform/$name"

  local sha size
  sha="$(sha256sum "$SERVER_ROOT/Games/$platform/$name" | cut -d' ' -f1)"
  size="$(stat -c '%s' "$SERVER_ROOT/Games/$platform/$name")"
  add_manifest_entry "$platform" "/Games/$platform/$name" file "$size" "$sha" "$title"
}

add_manifest_entry() {
  local platform="$1" path="$2" type="$3" size="$4" sha="$5" title="$6"
  local manifest="$SERVER_ROOT/Games/.gotg/manifest.json"
  [[ -f "$manifest" ]] || echo '{"version":1,"games":[]}' >"$manifest"
  local tmp="$manifest.tmp"
  jq --arg pf "$platform" --arg p "$path" --arg t "$type" \
    --argjson s "$size" --arg sha "$sha" --arg title "$title" \
    '.games += [{platform:$pf, path:$p, type:$t, size_bytes:$s,
                 sha256:(if $sha == "" then null else $sha end), title:$title}]' \
    "$manifest" >"$tmp" && mv "$tmp" "$manifest"
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
  for lib in color common config api manifest download env launcher pads pads-dolphin keys saves ludusavi; do
    source "$GOTG_LIB/$lib.sh"
  done
  GOTG_SERVER="$GOTG_SERVER_URL"
  GOTG_USER="tester"
  GOTG_PASS="hunter2"
  GOTG_REMOTE_ROOT="/Games"
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
