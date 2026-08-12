# shellcheck shell=bash
# `gotg complete` — the lists the shell completion asks for.
#
# A command rather than letting the completion script read the catalog itself,
# so that how an id is derived from a path stays in one place. The script knows
# the shape of the command line; this knows the library.
#
# Two rules hold everywhere below, because this runs on a keypress:
#
#   - it never fetches. A tab that blocks on the network is worse than no
#     completion at all, so a catalog that is missing or stale is used as-is or
#     not at all — `manifest_ensure` is deliberately not called.
#   - it never fails loudly. Anything unexpected is an empty completion, not an
#     error printed over the command someone is still typing.

cmd_complete() {
  case "${1:-}" in
    ids) complete_ids ;;
    variants) complete_variants "${2:-}" ;;
    platforms) complete_platforms ;;
    *) return 0 ;;
  esac
}

# Both filtered against the contract before anything leaves this process:
# the platform list lands in `compgen -W`, which word-expands its list — a
# poisoned catalog carrying `x$(cmd)` as a platform would otherwise run cmd
# in the user's shell on TAB. Ids reach only mapfile today, but the same
# fence keeps a completion refactor from ever reopening this.
complete_ids() {
  manifest_cached || return 0
  manifest_games 2>/dev/null | jq -r '.id // empty' 2>/dev/null |
    grep -E "$GOTG_ID_RE" || true
}

complete_platforms() {
  manifest_cached || return 0
  jq -r '[.games[].platform] | unique | .[]' "$GOTG_CACHE_FILE" 2>/dev/null |
    grep -E "$GOTG_PLATFORM_RE" || true
}

# The variants of one game. The id is whatever is on the command line, which may
# be half-typed or nonsense, so this resolves it by exact match and gives up
# quietly rather than going through manifest_find, which is allowed to die.
complete_variants() {
  local id="$1" platform
  [[ -n "$id" ]] || return 0
  manifest_cached || return 0

  platform="$(manifest_games 2>/dev/null |
    jq -r --arg i "$id" 'select(.id == $i) | .platform' 2>/dev/null | head -1)"
  [[ -n "$platform" ]] || return 0

  env_variant_names "$platform" "$id" 2>/dev/null || true
}
