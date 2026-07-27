# shellcheck shell=bash
# Test harness: an isolated HOME plus a mock File Browser, so nothing here can
# reach the real server or touch the real ~/Games.

setup_env() {
  export TEST_TMP="$BATS_TEST_TMPDIR"
  export SERVER_ROOT="$TEST_TMP/server"
  export GOTG_CONFIG_DIR="$TEST_TMP/config"
  export GOTG_STATE_DIR="$TEST_TMP/state"
  export GOTG_GAMES_DIR="$TEST_TMP/Games"
  export GOTG_CONFIG_FILE="$GOTG_CONFIG_DIR/config.json"
  export GOTG_CACHE_FILE="$GOTG_STATE_DIR/manifest.json"
  export GOTG_PARTIAL_DIR="$GOTG_GAMES_DIR/.gotg-partial"
  export GOTG_ROOTS_DIR="$GOTG_STATE_DIR/roots"
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
