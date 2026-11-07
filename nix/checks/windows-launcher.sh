#!/usr/bin/env bash
# gotg.exe under Wine (checks.windowsLauncher): --version, a dry run's
# command line, and a whole launch against the real service -- a token
# login on a pipe, the download, the seed config and an ini action, and the
# "emulator" (a second gotg.exe, asked for --help) started and waited on.
#   $1 gotg.exe   $2 gotg-proxy   $3 wine   $4 scratch directory
set -euo pipefail
exe="$1" proxy="$2" wine="$3" work="$4"

export WINEPREFIX="$work/prefix" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
mkdir -p "$WINEPREFIX"
fail() {
  echo "FAIL: $*" >&2
  [[ ! -f "$work/service.log" ]] || tail -n 30 "$work/service.log" >&2
  exit 1
}
# Z: is Wine's view of /: a Unix path as gotg.exe sees it.
winpath() { printf 'Z:%s' "${1//\//\\}"; }
gotg() { "$wine" "$work/bundle/gotg.exe" "$@" 2>"$work/stderr"; }

version="$("$wine" "$exe" --version 2>/dev/null | tr -d '\r')"
[[ "$version" == "gotg "*"(x86_64-windows)" ]] || fail "--version said: $version"
echo "ok: $version"

# The service, as the client tests run it.
port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
url="http://127.0.0.1:$port"
mkdir -p "$work/library/n64" "$work/files" "$work/state"
env PORT="$port" GOTG_PROXY_TOKEN=test-token GOTG_INDEX_TOKEN=index-token \
  GOTG_SAVES_DIR="$work/saves" GOTG_FILES_DIR="$work/files" GOTG_LIBRARY_ROOTS="$work/library" \
  GOTG_CATALOG_DB="$work/state/catalog.db" GOTG_TOKENS_DB="$work/state/tokens.db" \
  GOTG_ART_DIR="$work/art" GOTG_LOGS_DIR="$work/logs" GOTG_PORTS_DIR="$work/ports" \
  GOTG_ADMIN_TOKEN=admin-token GOTG_LIBRARY_TOKEN=library-token \
  "$proxy" >"$work/service.log" 2>&1 &
trap 'kill $! 2>/dev/null || true' EXIT
for _ in $(seq 100); do
  curl -fs -o /dev/null "$url/healthz" && break
  sleep 0.2
done
curl -fs -o /dev/null "$url/healthz" || fail "the service did not start"

name="A Game (USA).z64"
printf 'N64 ROM BYTES' >"$work/library/n64/$name"
sha="$(sha256sum "$work/library/n64/$name" | cut -d' ' -f1)"
jq -n --arg n "$name" --arg p "$work/library/n64/$name" --arg sha "$sha" \
  '{handler: "single_file", title: "A Game",
    files: [{name: $n, path: $p, size_bytes: 13, mtime: 1700000000, sha256: $sha}]}' |
  curl -fsS -X PUT -H "Authorization: Bearer index-token" --data-binary @- \
    "$url/catalog/n64/usa.a_game" >/dev/null

mkdir -p "$work/bundle/emulator" "$work/bundle/config/0"
cp "$exe" "$work/bundle/gotg.exe"
cp "$exe" "$work/bundle/emulator/emu.exe"
printf 'seeded\n' >"$work/bundle/config/0/settings.bml"
jq -n --arg url "$url" --arg n "$name" --arg sha "$sha" '{
  version: 1, server: $url, files_urls: [$url], attr: "env-n64", variant: "",
  output: "n64.usa.a_game",
  game: {id: "usa.a_game", platform: "n64", handler: "single_file", title: "A Game",
         files: [{name: $n, size_bytes: 13, sha256: $sha}]},
  windows: {
    version: 1, name: "env-n64", exe: "emulator/emu.exe",
    args: ["--help", "{fullscreen}", "--setting", "Paths/Saves={state}/saves/", "{target}"],
    fullscreen_flag: "--fullscreen", env: {}, config_root: "state",
    config_files: {"Ares/settings.bml": "config/0/settings.bml"},
    actions: [{ini: {file: "{state}/User/Dolphin.ini", section: "AutoUpdate", key: "UpdateTrack", value: ""}}],
    saves: ["saves/**"], excludes: [], states: null, saves_dirs: ["saves"],
    keys: null, firmware: null, native_port: false,
    game_version_min: null, game_version_max: null}}' >"$work/bundle/spec.json"

GOTG_CONFIG_DIR="$(winpath "$work/appdata")" GOTG_LOCAL_DIR="$(winpath "$work/local")"
export GOTG_CONFIG_DIR GOTG_LOCAL_DIR
state="$(winpath "$work/local/env/env-n64")"
target="$(winpath "$work/local/games/n64/$name")"

# The dry run: Windows paths, backslashes, the literal slashes kept.
out="$(gotg --dry-run | tr -d '\r')" || fail "dry run: $(cat "$work/stderr")"
echo "$out"
want="run: $(winpath "$work/bundle/emulator/emu.exe") --help --fullscreen --setting Paths/Saves=$state/saves/ \"$target\""
grep -qxF "$want" <<<"$out" || fail "dry run, wanted: $want"
out="$(gotg --dry-run --windowed | tr -d '\r')" || fail "windowed dry run"
grep -q -- "--fullscreen" <<<"$out" && fail "--windowed kept the flag"

gotg </dev/null && fail "launch before login succeeded"
grep -q "run: gotg login" "$work/stderr" || fail "not logged in: $(cat "$work/stderr")"

echo test-token | gotg login --token || fail "login: $(cat "$work/stderr")"
jq -e --arg url "$url" '.url == $url and .token == "test-token"' "$work/appdata/api.json" >/dev/null ||
  fail "api.json: $(cat "$work/appdata/api.json")"

gotg -- --extra </dev/null >"$work/stdout" || fail "launch: $(cat "$work/stderr")"
cmp "$work/library/n64/$name" "$work/local/games/n64/$name" || fail "the download differs"
grep -q "^usage: gotg" "$work/stdout" || fail "the emulator did not run: $(cat "$work/stdout")"
[[ -d "$work/local/env/env-n64/saves" ]] || fail "no saves dir"
cmp "$work/bundle/config/0/settings.bml" "$work/local/env/env-n64/Ares/settings.bml" || fail "not seeded"
[[ "$(tr -d '\r' <"$work/local/env/env-n64/User/Dolphin.ini")" == "$(printf '[AutoUpdate]\nUpdateTrack = ')" ]] ||
  fail "ini: $(cat "$work/local/env/env-n64/User/Dolphin.ini")"
echo "ok: launched under Wine"
