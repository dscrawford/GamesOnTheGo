#!/usr/bin/env bash
# A real bundle under Wine (checks.windowsBundle): the fixture library's
# gb.usa.tetris_2 zip, as `nix build <library>#gb.usa.tetris_2.gotgWindows`
# makes it, accepted by the server's port store, unzipped, signed in, its
# game downloaded from the real service, its command line rendered, and its
# ares.exe started under Xvfb on a generated Game Boy ROM, seen running it.it.
#   $1 the zip   $2 gotg-proxy   $3 wine   $4 scratch directory
set -euo pipefail
zip="$1" proxy="$2" wine="$3" work="$4"
output=gb.usa.tetris_2 top=gotg-gb.usa.tetris_2

export WINEPREFIX="$work/prefix" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
mkdir -p "$WINEPREFIX"
fail() {
  echo "FAIL: $*" >&2
  [[ ! -f "$work/service.log" ]] || tail -n 30 "$work/service.log" >&2
  [[ ! -f "$work/stderr" ]] || tail -n 30 "$work/stderr" >&2
  exit 1
}
winpath() { printf 'Z:%s' "${1//\//\\}"; }

port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
url="http://127.0.0.1:$port"
mkdir -p "$work/library/gb" "$work/files" "$work/state"
env PORT="$port" GOTG_PROXY_TOKEN=test-token GOTG_INDEX_TOKEN=index-token \
  GOTG_SAVES_DIR="$work/saves" GOTG_FILES_DIR="$work/files" GOTG_LIBRARY_ROOTS="$work/library" \
  GOTG_CATALOG_DB="$work/state/catalog.db" GOTG_TOKENS_DB="$work/state/tokens.db" \
  GOTG_ART_DIR="$work/art" GOTG_LOGS_DIR="$work/logs" GOTG_PORTS_DIR="$work/ports" \
  GOTG_ADMIN_TOKEN=admin-token GOTG_LIBRARY_TOKEN=library-token \
  "$proxy" >"$work/service.log" 2>&1 &
svc=$!
trap 'kill $svc 2>/dev/null || true; "${wine%/*}/wineserver" -k 2>/dev/null || true' EXIT
for _ in $(seq 100); do
  curl -fs -o /dev/null "$url/healthz" && break
  sleep 0.2
done
curl -fs -o /dev/null "$url/healthz" || fail "the service did not start"

# The server's own check of a Windows upload (ports.check_bundle), as
# `gotg port push --windows` sends it.
push() {
  curl -sS -T "$1" -H "Authorization: Bearer test-token" -H "X-Gotg-Sha256: $(sha256sum "$1" | cut -d' ' -f1)" \
    -H "X-Gotg-Device: check" -H "Content-Type: application/zip" -o "$work/push.json" -w '%{http_code}' \
    "$url/ports/$2/win"
}
code="$(push "$zip" "$output")"
[[ "$code" == 200 ]] || fail "the service refused the bundle: HTTP $code $(cat "$work/push.json")"
code="$(push "$zip" gb.usa.tetris_3)"
[[ "$code" == 4* ]] || fail "a bundle under another output's name was taken: HTTP $code"
echo "ok: the service took the $(($(stat -c %s "$zip") / 1048576)) MiB zip as $output's Windows build"

# A Game Boy ROM: ROM only, 32 KiB, a valid header checksum, and a loop
# that keeps the LCD on (the boot ROM's state) forever.
python3 - "$work/library/gb/Tetris 2 (USA).gb" <<'EOF'
import sys
rom = bytearray(32 * 1024)
rom[0x100:0x104] = bytes([0x00, 0xC3, 0x50, 0x01])  # nop; jp 0x150
rom[0x134:0x13C] = b"GOTGTEST"
rom[0x150:0x152] = bytes([0x18, 0xFE])  # jr -2
x = 0
for b in rom[0x134:0x14D]:
    x = (x - b - 1) & 0xFF
rom[0x14D] = x
open(sys.argv[1], "wb").write(rom)
EOF
name="Tetris 2 (USA).gb"
rom="$work/library/gb/$name"
sha="$(sha256sum "$rom" | cut -d' ' -f1)"
jq -n --arg n "$name" --arg p "$rom" --arg sha "$sha" \
  '{handler: "single_file", title: "Tetris 2",
    files: [{name: $n, path: $p, size_bytes: 32768, mtime: 1700000000, sha256: $sha}]}' |
  curl -fsS -X PUT -H "Authorization: Bearer index-token" --data-binary @- \
    "$url/catalog/gb/usa.tetris_2" >/dev/null

# Unzipped as a person would; the catalog entry and server are this one's.
unzip -q "$zip" -d "$work/unzipped"
b="$work/unzipped/$top"
[[ -f "$b/gotg.exe" && -f "$b/emulator/ares.exe" ]] || fail "the zip lacks gotg.exe or emulator/ares.exe"
jq --arg url "$url" --arg sha "$sha" \
  '.server = $url | .files_urls = [$url] | .game.files[0].sha256 = $sha | .game.files[0].size_bytes = 32768' \
  "$b/spec.json" >"$work/spec.json"
mv "$work/spec.json" "$b/spec.json"
gotg() { "$wine" "$b/gotg.exe" "$@" 2>"$work/stderr"; }

GOTG_CONFIG_DIR="$(winpath "$work/appdata")" GOTG_LOCAL_DIR="$(winpath "$work/local")"
export GOTG_CONFIG_DIR GOTG_LOCAL_DIR
echo test-token | gotg login --token || fail "login"

state="$(winpath "$work/local/env/env-gb")"
target="$(winpath "$work/local/games/gb/$name")"
out="$(gotg --dry-run | tr -d '\r')" || fail "dry run"
echo "$out"
grep -qxF "cwd: $state" <<<"$out" || fail "dry run: the cwd is not {state}"
want="run: $(winpath "$b/emulator/ares.exe") --fullscreen --settings-file $state/settings.bml"
want+=" --setting Paths/Saves=$state/saves/ --setting Paths/Home=$state/Systems/ --no-file-prompt"
want+=" --system \"Game Boy\" \"$target\""
grep -qxF "$want" <<<"$out" || fail "dry run, wanted: $want"
echo "ok: the bundle's command line"

# The real thing, windowed on a virtual display: downloaded, then ares runs
# the game until it is stopped, and gotg.exe outlives it. Xvfb gives Wine
# no OpenGL, and ares stops at its video driver's error before loading a
# game, so the test (only) adds GDI video and no audio after the bundle's args.
gotg --windowed -- --setting Video/Driver=GDI --setting Audio/Driver=None </dev/null >"$work/stdout" &
launch=$!
# ares titles its window with the game once it is loaded.
loaded() { xwininfo -root -tree | grep -qF '"Tetris 2 (USA)": ("ares.exe"'; }
up=""
for _ in $(seq 90); do
  sleep 1
  kill -0 "$launch" 2>/dev/null || break
  if loaded; then
    up=1
    break
  fi
done
[[ -n "$up" ]] || fail "ares.exe did not load the game"
# Running, not just started: still there ten seconds on.
sleep 10
loaded || fail "ares.exe stopped early"
cmp "$rom" "$work/local/games/gb/$name" || fail "the download differs"
pkill -f 'ares.exe' || true
wait "$launch" || true
[[ -f "$work/local/env/env-gb/settings.bml" ]] || fail "ares did not write {state}/settings.bml"
tr -d '\r' <"$work/stderr" | grep -q "starting Tetris 2" || fail "gotg.exe never said it started the game"
tr -d '\r' <"$work/stderr" | grep -q "ares.exe exited with" || fail "gotg.exe did not outlive ares"
echo "ok: ares.exe loaded the game under Wine and ran it for 10 s"
