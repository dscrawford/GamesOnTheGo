# shellcheck shell=bash
# `gotg port`: a game built here as one file that runs without Nix, kept on
# the server for the machines that have none (src/gotg/ports.py).
#
#   gotg port push <id> [variant]   build <library>#<output>.gotgPortable, upload it
#   gotg port list                  what this account has pushed
#   gotg port pull <id> [variant] [-o file]   download one
#   gotg port rm <id> [variant]     drop one from the server
#
# The build is nix/portable.nix's; the server keeps the newest of each output,
# per account. The web app lists them on the game's page with a Download.

port_usage() {
  cat <<'EOF'
usage: gotg port <command>

  push <id> [variant]          build the game as one file that runs without Nix,
                               and keep it on the server (the web app's Download)
  list                         what this account has pushed
  pull <id> [variant] [-o <file>]
                               download one (default: ./gotg-<output>.run)
  rm <id> [variant]            drop one from the server
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
  local library output root file sha size http out
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
  size="$(stat -c %s "$file")"
  sha="$(sha256sum "$file" | cut -d' ' -f1)"
  log "uploading $output ($((size / 1048576)) MiB)"
  out="$(mktemp)"
  # -T streams the file: --data-binary would read gigabytes into memory first.
  http="$(service_curl -sS --max-time "${GOTG_PORT_UPLOAD_TIMEOUT:-7200}" -T "$file" \
    -H "X-Gotg-Sha256: $sha" -H "X-Gotg-Device: $(device_name)" -H "Content-Type: application/octet-stream" \
    -o "$out" -w '%{http_code}' "$(service_url)/ports/$output")" || {
    rm -f "$out"
    die "could not reach the GOTG service at $(service_url)"
  }
  case "$http" in
    200)
      rm -f "$out"
      log "$output is on the server: its page in the web app has the download"
      ;;
    *)
      local detail
      detail="$(jq -r '.error // "no detail"' "$out" 2>/dev/null || echo "no detail")"
      rm -f "$out"
      die "the GOTG service answered HTTP $http pushing $output: $detail"
      ;;
  esac
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
  jq -r '.ports[] | "\(.output)\t\(.size / 1048576 | floor) MiB\t\(.device)\t\(.written_at)"' "$out"
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
