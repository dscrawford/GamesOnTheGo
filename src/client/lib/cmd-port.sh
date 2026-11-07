# shellcheck shell=bash
# `gotg port`: a game built here as one file that runs without Nix, kept on
# the server for the machines that have none (src/gotg/ports.py).
#
#   gotg port push <id> [variant]   build <library>#<output>.gotgPortable, upload it
#   gotg port list                  what this account has pushed
#   gotg port pull <id> [variant] [-o file]   download one
#   gotg port rm <id> [variant]     drop one from the server
#
# --windows on push, pull and rm means the output's Windows zip instead
# (`.gotgWindows`, nix/windows/bundle.nix; docs/windows.md).
#
# The build is nix/portable.nix's; the server keeps the newest of each output
# and kind, per account. The web app lists them on the game's page with a Download.

port_usage() {
  cat <<'EOF'
usage: gotg port <command>

  push [--windows] <id> [variant]
                               build the game as one file that runs without Nix,
                               and keep it on the server (the web app's Download)
  list                         what this account has pushed
  pull [--windows] <id> [variant] [-o <file>]
                               download one (default: ./gotg-<output>.run)
  rm [--windows] <id> [variant]
                               drop one from the server

  --windows                    the game's Windows zip instead: gotg.exe and the
                               emulator's Windows build (./gotg-<output>-win64.zip)
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
  [[ -n "$game" ]] || die "which game? usage: gotg port push [--windows] <id> [variant]"
  local entry
  entry="$(manifest_find "$game")"
  library_attr "$entry" "$variant"
}

# <id> [variant] and --windows, in any order, into the caller's game, variant
# and windows; $1 is the usage a stray argument dies with.
port_args() {
  local usage="$1"
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --windows) windows=1 ;;
      -*) die "usage: $usage" ;;
      *)
        if [[ -z "$game" ]]; then game="$1"; elif [[ -z "$variant" ]]; then variant="$1"; else die "usage: $usage"; fi
        ;;
    esac
    shift
  done
}

# Where an output's build of each kind lives on the server.
port_route() {
  local output="$1" windows="${2:-}"
  printf '%s/ports/%s%s' "$(service_url)" "$output" "${windows:+/win}"
}

port_push() {
  local game="" variant="" windows=""
  port_args "gotg port push [--windows] <id> [variant]" "$@"
  port_need_service
  local library output root file sha size http out attr what type
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library to build from: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  output="$(port_output "$game" "$variant")"
  mkdir -p "$GOTG_STATE_DIR/ports"
  if [[ -n "$windows" ]]; then
    attr="$output.gotgWindows" what="the Windows build of $output" type=application/zip
    root="$GOTG_STATE_DIR/ports/$output-win64.zip"
    log "building $output as a zip that plays on Windows (the first build of a game takes a while)"
  else
    attr="$output.gotgPortable" what="$output" type=application/octet-stream
    root="$GOTG_STATE_DIR/ports/$output"
    log "building $output as one file that runs without Nix (the first build of a game takes a while)"
  fi
  _nix_drawn build "$library#$attr" -o "$root" ||
    die "could not build $attr from $library"
  file="$(readlink -f "$root")"
  [[ -f "$file" ]] || die "the build of $what left no file at $root"
  size="$(stat -c %s "$file")"
  sha="$(sha256sum "$file" | cut -d' ' -f1)"
  log "uploading $what ($((size / 1048576)) MiB)"
  out="$(mktemp)"
  # -T streams the file: --data-binary would read gigabytes into memory first.
  http="$(service_curl -sS --max-time "${GOTG_PORT_UPLOAD_TIMEOUT:-7200}" -T "$file" \
    -H "X-Gotg-Sha256: $sha" -H "X-Gotg-Device: $(device_name)" -H "Content-Type: $type" \
    -o "$out" -w '%{http_code}' "$(port_route "$output" "$windows")")" || {
    rm -f "$out"
    die "could not reach the GOTG service at $(service_url)"
  }
  case "$http" in
    200)
      rm -f "$out"
      log "$what is on the server: its page in the web app has the download"
      ;;
    *)
      local detail
      detail="$(jq -r '.error // "no detail"' "$out" 2>/dev/null || echo "no detail")"
      rm -f "$out"
      die "the GOTG service answered HTTP $http pushing $what: $detail"
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
  # The kind last, so the first four columns read as they did before there were two.
  jq -r '.ports[] | "\(.output)\t\(.size / 1048576 | floor) MiB\t\(.device)\t\(.written_at)\t\(if .kind == "win" then "windows" else "linux" end)"' "$out"
  rm -f "$out"
}

port_pull() {
  local game="" variant="" dest="" windows="" rest=()
  local usage="gotg port pull [--windows] <id> [variant] [-o <file>]"
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
        rest+=("$1")
        shift
        ;;
    esac
  done
  port_args "$usage" "${rest[@]}"
  port_need_service
  local output http what flag=""
  output="$(port_output "$game" "$variant")"
  if [[ -n "$windows" ]]; then
    what="the Windows build of $output" flag="--windows "
    [[ -n "$dest" ]] || dest="./gotg-$output-win64.zip"
  else
    what="$output"
    [[ -n "$dest" ]] || dest="./gotg-$output.run"
  fi
  rm -f "$dest.part"
  http="$(service_curl -sS --max-time "${GOTG_PORT_UPLOAD_TIMEOUT:-7200}" -o "$dest.part" -w '%{http_code}' \
    "$(port_route "$output" "$windows")/file")" || die "could not download $what"
  case "$http" in
    200 | 206)
      mv "$dest.part" "$dest"
      if [[ -n "$windows" ]]; then
        log "$dest: unzip it anywhere on Windows, run \`gotg.exe login\` once from a terminal, then gotg.exe"
      else
        chmod +x "$dest"
        log "$dest: run it with \`$dest login\` once on a new machine, then \`$dest\`"
      fi
      ;;
    404)
      rm -f "$dest.part"
      die "nothing has been pushed for $what; on a machine with Nix: gotg port push $flag$game${variant:+ $variant}"
      ;;
    *)
      rm -f "$dest.part"
      die "the GOTG service answered HTTP $http for $what"
      ;;
  esac
}

port_rm() {
  local game="" variant="" windows=""
  port_args "gotg port rm [--windows] <id> [variant]" "$@"
  port_need_service
  local output http what
  output="$(port_output "$game" "$variant")"
  what="$output"
  [[ -z "$windows" ]] || what="the Windows build of $output"
  http="$(service_curl -sS --max-time 60 -X DELETE -o /dev/null -w '%{http_code}' \
    "$(port_route "$output" "$windows")")" || die "could not reach the GOTG service at $(service_url)"
  case "$http" in
    200) log "dropped $what from the server" ;;
    404) die "nothing has been pushed for $what" ;;
    *) die "the GOTG service answered HTTP $http dropping $what" ;;
  esac
}
