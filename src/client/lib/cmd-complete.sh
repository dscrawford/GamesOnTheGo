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
    disabled) complete_disabled "${2:-}" ;;
    versions) complete_versions "${2:-}" "${3:-}" ;;
    platforms) complete_platforms ;;
    ready) complete_ready "${2:-}" "${3:-}" ;;
    installed) complete_installed ;;
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

# Whether one game would launch without building or downloading anything:
# the environment root is built and the game's bytes are here. Pure filesystem
# — same rules as everything else in this file — so the UI can ask it per
# selection without a nix evaluation or a network round trip. Exit 0 is ready;
# 1 is "preparing would do work"; the distinction IS the answer, so unlike the
# rest of this file the exit code is meaningful.
complete_ready() {
  local want="$1" variant="${2:-}" platform="" id game attr
  [[ -n "$want" ]] || return 1
  [[ -s "$GOTG_CACHE_FILE" ]] || return 1

  # The UI always qualifies (222 ids are on more than one platform); a bare id
  # is accepted for a person poking at it, first match wins as in completion.
  if [[ "$want" == */* ]]; then
    platform="${want%%/*}"
    id="${want#*/}"
  else
    id="$want"
  fi
  # One jq pass, version check folded in — not manifest_cached + manifest_games,
  # which each parse the whole 2MB manifest again. This runs synchronously on
  # every confirm press in the UI, and the three-parse version measured ~270ms
  # a pick on a desktop; call it a second on a Deck, frozen under the button.
  game="$(jq -c --arg i "$id" --arg p "$platform" \
    'if .version != 2 then empty else
       (.games[] | select(.id == $i and ($p == "" or .platform == $p)))
     end' "$GOTG_CACHE_FILE" 2>/dev/null | head -1 || true)"
  [[ -n "$game" ]] || return 1

  attr="$(env_attr "$game" "$variant" 2>/dev/null)" || return 1
  env_is_built "$attr" || return 1
  # Built, but by an older gotg: the launch would rebuild it first, with
  # nothing on screen -- the picker has already handed the display over. On
  # a Deck in Game Mode that was a black screen for as long as the build
  # took, right after every update. Not ready, so the picker shows its own
  # loading screen for the rebuild instead.
  env_is_current "$attr" || return 1
  game_is_installed "$game" || return 1
  # A positive token, not just exit 0: a client too old to know `ready` falls
  # through this file's catch-all `*) return 0` and would read as ready — the
  # UI requires the word as well as the code.
  printf 'ready\n'
}

# platform/id per line, from the same listing gotg list marks rows with, so
# the grid and list cannot disagree about what is here.
complete_installed() { manifest_installed_keys; }

# The versions of one game that are installed here, newest first, and which
# one a launch would run. One line each: "<version>" with a leading "*" on the
# one that would run. Pure filesystem, like everything else in this file, so
# the picker can ask the moment a menu opens.
complete_versions() {
  local want="$1" variant="${2:-}" game attr running version
  [[ -n "$want" ]] || return 0
  manifest_cached || return 0
  game="$(manifest_find "$want" 2>/dev/null)" || return 0
  [[ -n "$game" ]] || return 0
  attr="$(env_attr "$game" "$variant" 2>/dev/null)" || attr=""
  # Outside the substitution: versions_resolve dies for a mod no installed
  # version suits, and an exit inside $( ) does not reach an || in there.
  running="$(versions_resolve "$game" "$attr" 2>/dev/null)" || running=""
  while IFS= read -r version; do
    [[ -n "$version" ]] || continue
    if [[ "$version" == "$running" ]]; then
      printf '*%s\n' "$version"
    else
      printf '%s\n' "$version"
    fi
  done < <(versions_names "$game")
}

complete_variants() {
  local game
  game="$(_complete_game "$1")" || return 0
  [[ -n "$game" ]] || return 0
  # The ones a version window rules out are left off: a mod with nothing here
  # it can patch has no launch to complete to, and offering it would complete
  # to a paragraph explaining why not.
  versions_variants_runnable "$game" 2>/dev/null || true
}

# The other half, for the picker: which of a game's variants cannot run here.
# Usually nothing, which is why it is the short list of the two to send.
complete_disabled() {
  local game
  game="$(_complete_game "$1")" || return 0
  [[ -n "$game" ]] || return 0
  versions_variants_disabled "$game" 2>/dev/null || true
}

# The catalog entry behind whatever is on the command line, which may be
# half-typed or nonsense: resolved by exact match, qualified or not, and given
# up on quietly rather than through manifest_find, which is allowed to die.
_complete_game() {
  local want="$1" platform="" id
  [[ -n "$want" ]] || return 1
  manifest_cached || return 1
  if [[ "$want" == */* ]]; then
    platform="${want%%/*}"
    id="${want#*/}"
  else
    id="$want"
  fi
  manifest_games 2>/dev/null |
    jq -c --arg i "$id" --arg p "$platform" \
      'select(.id == $i and ($p == "" or .platform == $p))' 2>/dev/null | head -1
}
