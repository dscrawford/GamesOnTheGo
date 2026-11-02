# shellcheck shell=bash
# `gotg port`: a game built here as one file that runs without Nix, kept on
# the server for the machines that have none (src/gotg/ports.py).
#
#   gotg port push <id> [variant]   build <library>#<output>.gotgPortable, upload it
#   gotg port list                  what this account has pushed
#   gotg port pull <id> [variant] [-o file]   download one
#   gotg port rm <id> [variant]     drop one from the server
#   gotg port worker [--once]       build what the web app asks to play in a tab
#
# The build is nix/portable.nix's; the server keeps the newest of each output,
# per account. The web app lists them on the game's page with a Download.
#
# The worker is the other half of a game played in the browser as a port
# (src/client/env/web): pressing Play files a request on the server, and a
# machine with Nix running this claims it, builds the game for the browser
# against the ROM it downloads like any install, and pushes the one file back.
# The ROM never leaves this machine except as that build, to the same account.

port_usage() {
  cat <<'EOF'
usage: gotg port <command>

  push <id> [variant]          build the game as one file that runs without Nix,
                               and keep it on the server (the web app's Download)
  list                         what this account has pushed
  pull <id> [variant] [-o <file>]
                               download one (default: ./gotg-<output>.run)
  rm <id> [variant]            drop one from the server
  worker [--once]              build the games the web app asks to play in a
                               browser tab, as they are asked for (--once: what
                               is waiting now, then exit)
EOF
}

cmd_port() {
  local sub="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  case "$sub" in
    push) port_push "$@" ;;
    list | ls) port_list "$@" ;;
    pull) port_pull "$@" ;;
    rm | remove) port_rm "$@" ;;
    worker) port_worker "$@" ;;
    help | -h | --help | "") port_usage ;;
    *)
      printf 'error: unknown port command: %s\n\n' "$sub" >&2
      port_usage >&2
      exit 1
      ;;
  esac
}

port_need_service() {
  service_have || die "no GOTG service is configured; run: gotg login"
}

# The output a game and variant are, as the server names it: the library attr.
port_output() {
  local game="${1:-}" variant="${2:-}"
  [[ -n "$game" ]] || die "which game? usage: gotg port push <id> [variant]"
  local entry
  entry="$(manifest_find "$game")"
  library_attr "$entry" "$variant"
}

port_push() {
  local game="${1:-}" variant="${2:-}"
  port_need_service
  local library output root file
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library to build from: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  output="$(port_output "$game" "$variant")"
  root="$GOTG_STATE_DIR/ports/$output"
  mkdir -p "$GOTG_STATE_DIR/ports"
  log "building $output as one file that runs without Nix (the first build of a game takes a while)"
  _nix_drawn build "$library#$output.gotgPortable" -o "$root" ||
    die "could not build $output.gotgPortable from $library"
  file="$(readlink -f "$root")"
  [[ -f "$file" ]] || die "the build of $output left no file at $root"
  port_upload "$file" "ports/$output" "$output"
  log "$output is on the server: its page in the web app has the download"
}

# Stream one file to a PUT route of /ports. Dies with the server's reason.
port_upload() {
  local file="$1" route="$2" what="$3" size sha out http
  size="$(stat -c %s "$file")"
  sha="$(sha256sum "$file" | cut -d' ' -f1)"
  log "uploading $what ($((size / 1048576)) MiB)"
  out="$(mktemp)"
  # -T streams the file: --data-binary would read gigabytes into memory first.
  http="$(service_curl -sS --max-time "${GOTG_PORT_UPLOAD_TIMEOUT:-7200}" -T "$file" \
    -H "X-Gotg-Sha256: $sha" -H "X-Gotg-Device: $(device_name)" -H "Content-Type: application/octet-stream" \
    -o "$out" -w '%{http_code}' "$(service_url)/$route")" || {
    rm -f "$out"
    die "could not reach the GOTG service at $(service_url)"
  }
  if [[ "$http" != 200 ]]; then
    local detail
    detail="$(jq -r '.error // "no detail"' "$out" 2>/dev/null || echo "no detail")"
    rm -f "$out"
    die "the GOTG service answered HTTP $http pushing $what: $(printable "$detail")"
  fi
  rm -f "$out"
}

port_list() {
  port_need_service
  local out http
  out="$(mktemp)"
  http="$(service_curl -sS --max-time 60 -o "$out" -w '%{http_code}' "$(service_url)/ports")" || {
    rm -f "$out"
    die "could not reach the GOTG service at $(service_url)"
  }
  if [[ "$http" != 200 ]]; then
    rm -f "$out"
    die "the GOTG service answered HTTP $http listing ports"
  fi
  jq -r '.ports[] | "\(.output)\(if .kind == "web" then " (browser)" else "" end)\t\(.size / 1048576 | floor) MiB\t\(.device)\t\(.written_at)"' "$out"
  rm -f "$out"
}

port_pull() {
  local game="" variant="" dest=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o)
        dest="${2:-}"
        shift 2 || shift
        ;;
      -o*)
        dest="${1#-o}"
        shift
        ;;
      *)
        if [[ -z "$game" ]]; then game="$1"; elif [[ -z "$variant" ]]; then variant="$1"; else die "usage: gotg port pull <id> [variant] [-o <file>]"; fi
        shift
        ;;
    esac
  done
  port_need_service
  local output http
  output="$(port_output "$game" "$variant")"
  [[ -n "$dest" ]] || dest="./gotg-$output.run"
  rm -f "$dest.part"
  http="$(service_curl -sS --max-time "${GOTG_PORT_UPLOAD_TIMEOUT:-7200}" -o "$dest.part" -w '%{http_code}' \
    "$(service_url)/ports/$output/file")" || die "could not download $output"
  case "$http" in
    200 | 206)
      mv "$dest.part" "$dest"
      chmod +x "$dest"
      log "$dest: run it with \`$dest login\` once on a new machine, then \`$dest\`"
      ;;
    404)
      rm -f "$dest.part"
      die "nothing has been pushed for $output; on a machine with Nix: gotg port push $game${variant:+ $variant}"
      ;;
    *)
      rm -f "$dest.part"
      die "the GOTG service answered HTTP $http for $output"
      ;;
  esac
}

port_rm() {
  local game="${1:-}" variant="${2:-}"
  port_need_service
  local output http
  output="$(port_output "$game" "$variant")"
  http="$(service_curl -sS --max-time 60 -X DELETE -o /dev/null -w '%{http_code}' \
    "$(service_url)/ports/$output")" || die "could not reach the GOTG service at $(service_url)"
  case "$http" in
    200) log "dropped $output from the server" ;;
    404) die "nothing has been pushed for $output" ;;
    *) die "the GOTG service answered HTTP $http dropping $output" ;;
  esac
}

# --- the worker: browser builds, built when the web app asks ----------------

port_worker() {
  local once="" interval="${GOTG_PORT_WORKER_INTERVAL:-15}"
  case "${1:-}" in
    --once) once=1 ;;
    "") ;;
    *) die "usage: gotg port worker [--once]" ;;
  esac
  port_need_service
  local library
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library to build from: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  [[ -n "$once" ]] || log "waiting for the web app to ask for a game in the browser (Ctrl-C stops)"
  local listing out http output
  while :; do
    out="$(mktemp)"
    if http="$(service_curl -sS --max-time 60 -o "$out" -w '%{http_code}' "$(service_url)/ports/requests")" &&
      [[ "$http" == 200 ]]; then
      listing="$(jq -r '.requests[].output' "$out" 2>/dev/null)" || listing=""
      for output in $listing; do
        # Each in a subshell: one game's `die` is that game's failure, not the worker's.
        (port_web_build "$library" "$output") || warn "the browser build of $output failed"
      done
    else
      warn "could not ask $(service_url) what to build${http:+ (HTTP $http)}"
    fi
    rm -f "$out"
    [[ -z "$once" ]] || return 0
    sleep "$interval"
  done
}

# POST to /ports/<output>/web/<action>; the HTTP status on stdout.
port_web_post() {
  local output="$1" action="$2" body="${3:-}"
  service_curl -sS --max-time 60 -X POST -o /dev/null -w '%{http_code}' \
    -H "X-Gotg-Device: $(device_name)" -H "Content-Type: application/json" \
    ${body:+--data-binary "$body"} "$(service_url)/ports/$output/web/$action"
}

# Claim one request, build it, push it -- or tell the server why not.
port_web_build() {
  local library="$1" output="$2" http
  # The server names outputs by OUTPUT_RE; this worker builds only a game's own.
  [[ "$output" =~ ^[a-z0-9][a-z0-9_-]{0,15}\.[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$ ]] || {
    warn "not a game this worker builds: $(printable "$output")"
    return 0
  }
  http="$(port_web_post "$output" claim)" || return 1
  # 409: another worker took it first, or it was built meanwhile.
  [[ "$http" == 200 ]] || return 0
  log "building $output for the browser"

  local work logf status=0
  mkdir -p "$GOTG_STATE_DIR/ports"
  work="$(mktemp -d "$GOTG_STATE_DIR/ports/.web-XXXXXX")"
  logf="$work/build.log"
  (_port_web_build_steps "$library" "$output" "$work") 2> >(tee "$logf" >&2) || status=$?
  wait # the tee
  if ((status != 0)); then
    local reason
    reason="$(tail -c 1500 "$logf" | sed 's/\x1b\[[0-9;]*m//g')"
    http="$(port_web_post "$output" fail "$(jq -cn --arg e "$reason" '{error: $e}')")" ||
      warn "could not tell the server the build of $output failed"
    rm -rf "$work"
    return 1
  fi
  rm -rf "$work"
  log "$output is on the server: Play in the web app runs it in the tab"
}

_port_web_build_steps() {
  local library="$1" output="$2" work="$3" game builder bin rom
  manifest_ensure
  game="$(manifest_find "${output%%.*}/${output#*.}")"
  [[ -n "$game" ]] || die "$output is not in the catalog"
  # The ROM, as `gotg install` gets it: it stays on this machine.
  download_game "$game"
  rom="$(game_installed_path "$game")" || die "$output downloaded, but is not where it was put"
  builder="$GOTG_STATE_DIR/ports/$output.web-builder"
  "$(nix_bin)" build "$library#$output.gotgWeb" -o "$builder" ||
    die "$library has no browser build of $output (or it did not build)"
  bin="$(find -L "$builder/bin" -maxdepth 1 -type f -perm -u+x | first_line)"
  [[ -n "$bin" ]] || die "the browser builder for $output has no program"
  "$bin" "$rom" "$work/game.js"
  port_upload "$work/game.js" "ports/$output/web" "$output (browser)"
}
