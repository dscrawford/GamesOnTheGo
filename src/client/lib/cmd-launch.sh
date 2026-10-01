# shellcheck shell=bash
# `gotg launch --spec <file>`: a game as Nix runs it (docs/nix-games.md).
#
# A per-game flake output bakes in a spec -- the catalog entry, the server,
# where its bytes are, the environment's name and its store path -- and runs
# this. What follows is `gotg play`'s own path, play_launch, unchanged:
# download, firmware, keys, saves, danstick, bindings, the session and the
# overlay. Only the lookups are the spec's: the game is not looked for in a
# cached catalog, the environment is not resolved, built or found under the
# GC roots -- Nix built it, and it is a dependency of the output that runs
# this.
#
#   {"version": 1, "server": "https://...", "files_urls": ["https://..."],
#    "attr": "env-n64-usa_donkey_kong_64", "variant": "",
#    "env": "/nix/store/...-gotg-env-env-n64-usa_donkey_kong_64",
#    "game": {"id": ..., "platform": ..., "handler": ..., "files": [...]}}

cmd_launch() {
  local spec="" want_version=""
  local -a rest=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --spec)
        spec="${2:-}"
        shift 2 || shift
        ;;
      --spec=*)
        spec="${1#--spec=}"
        shift
        ;;
      --version)
        want_version="${2:-}"
        shift 2 || shift
        ;;
      --version=*)
        want_version="${1#--version=}"
        shift
        ;;
      *)
        rest+=("$1")
        shift
        ;;
    esac
  done
  [[ -n "$spec" && -f "$spec" ]] || die "usage: gotg launch --spec <file> [--version v] [emulator args...]"
  jq -e '.version == 1' "$spec" >/dev/null 2>&1 ||
    die "$spec is not a version 1 launch spec"

  local game attr env server variant platform id
  game="$(jq -c '.game // empty' "$spec")"
  attr="$(jq -r '.attr // empty' "$spec")"
  env="$(jq -r '.env // empty' "$spec")"
  server="$(jq -r '.server // empty' "$spec")"
  variant="$(jq -r '.variant // empty' "$spec")"
  [[ -n "$game" ]] || die "$spec names no game"
  platform="$(manifest_field "$game" platform)"
  id="$(manifest_field "$game" id)"
  # Everything here becomes a path or an argument: checked before any of it
  # is used, exactly as a catalog entry is.
  validate_id "$id"
  validate_platform "$platform"
  validate_attr "$attr"
  [[ -z "$variant" || "$variant" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "invalid variant name in $spec: $variant"
  [[ "$env" == /* ]] || die "$spec names no environment path"
  [[ -x "$env/bin/gotg-play" ]] || die "no gotg-play in $env, the environment $spec names"

  # The token this machine holds is for the server it logged into. A game
  # from another one is refused rather than handed that token.
  if [[ -n "$server" ]] && saves_have_remote; then
    local here
    here="$(saves_api_url)"
    [[ "${server%/}" == "$here" ]] ||
      die "this game is from ${server%/}, and this machine is logged into $here.
     To play from there: gotg login ${server%/}"
  fi

  export GOTG_PINNED_GAME="$game" GOTG_PINNED_ATTR="$attr" GOTG_PINNED_ENV="$env"
  GOTG_PINNED_FILES_URLS="$(jq -r '(.files_urls // [])[]' "$spec")"
  export GOTG_PINNED_FILES_URLS

  play_launch "$platform/$id" "$variant" "$want_version" ${rest[@]+"${rest[@]}"}
}
