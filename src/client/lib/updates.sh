# shellcheck shell=bash
# What is out of date on this machine: gotg, the picker, and the games.
#
# A Deck once ran a day-old picker for a week after its library's pin had
# moved, and a fix that had shipped looked like one that did not work
# (CLAUDE.md: the pin alone is not what Steam runs). Nothing here said so.
# This is the saying: `gotg complete updates` answers in a moment from a
# cache and the filesystem, for the picker to draw a badge from, and `gotg
# update --check` is the work behind the cache -- the network and the
# evaluation -- for a worker to run now and then.
#
# What "out of date" means, each read off something real rather than a stamp:
#
#   gotg.behind    the library's locked `gotg` revision is not the head of
#                  where it came from (`nix flake metadata`, which Nix caches
#                  itself and fetches with the person's own tokens)
#   gotg.unbuilt   the picker's or the client's root is not what the library
#                  as locked would build -- the pin moved, `gotg update` did
#                  not run; the Deck's week
#   a game: build  one of its roots is not what the library as locked would
#                  build. Evaluated, never read off the root's .by stamp:
#                  that stamp is a hash of the whole flake.lock and flips on
#                  every catalog move, which would flag every game at once
#   a game: extras the catalog has attached a release the install lacks --
#                  an update, DLC, a texture pack (download.sh's top-up)
#
# The evaluation is only trusted for the lock it was made against: a cache
# from before the lock moved is `pending`, and pending says nothing, so no
# badge flashes between `gotg refresh` and the next check.

updates_cache_file() { printf '%s/updates.json' "$GOTG_STATE_DIR"; }
updates_system() { printf '%s-linux' "$(uname -m)"; }

# A root's attribute, platform.region.name[.variant], as the key the picker
# knows a game by: platform/region.name.
updates_attr_key() {
  local attr="$1" platform rest region name
  platform="${attr%%.*}"
  rest="${attr#*.}"
  region="${rest%%.*}"
  rest="${rest#*.}"
  name="${rest%%.*}"
  printf '%s/%s.%s' "$platform" "$region" "$name"
}

# Where the library's gotg came from, as a flake reference -- for a GitHub
# input, the one kind whose head is a cheap question. Anything else (a path,
# a git checkout: a developer's library) has no "latest" and says nothing.
updates_gotg_original() {
  jq -r '.nodes.gotg.original // {} | select(.type == "github")
         | "github:\(.owner)/\(.repo)" + (if .ref then "/" + .ref else "" end)' \
    "$1/flake.lock" 2>/dev/null || true
}

updates_gotg_locked() {
  jq -r '.nodes.gotg.locked.rev // empty' "$1/flake.lock" 2>/dev/null || true
}

# The head of that reference, or nothing when offline or refused.
updates_latest_rev() {
  timeout "${GOTG_UPDATE_EVAL_TIMEOUT:-120}" "$(nix_bin)" flake metadata --json "$1" 2>/dev/null |
    jq -r '.locked.rev // empty' 2>/dev/null || true
}

# What the library, as locked, would build for each root: attribute -> store
# path, null for an attribute the library no longer has. One evaluation for
# all of them. Each attribute has passed library_root_attrs' pattern, so what
# reaches the Nix string is letters, digits, dots, dashes and underscores.
updates_eval_games() {
  local library="$1"
  shift
  (($# > 0)) || {
    printf '{}'
    return 0
  }
  local attrs
  attrs="$(printf '%s\n' "$@" | jq -R . | jq -cs .)"
  timeout "${GOTG_UPDATE_EVAL_TIMEOUT:-120}" "$(nix_bin)" eval --json \
    "$library#legacyPackages.$(updates_system)" --apply "
      root:
      let
        walk = s: parts:
          if parts == [ ] then s
          else if builtins.isAttrs s && builtins.hasAttr (builtins.head parts) s
          then walk s.\${builtins.head parts} (builtins.tail parts)
          else null;
        want = attr:
          let
            parts = builtins.filter builtins.isString (builtins.split \"[.]\" attr);
            found = walk root parts;
            got = builtins.tryEval (if found == null then null else (found.outPath or null));
          in if got.success then got.value else null;
      in builtins.listToAttrs (map (attr: { name = attr; value = want attr; }) (builtins.fromJSON ''${attrs}''))
    " 2>/dev/null || true
}

# And for the client and the picker: { gotg, ui } as store paths.
updates_eval_apps() {
  timeout "${GOTG_UPDATE_EVAL_TIMEOUT:-120}" "$(nix_bin)" eval --json \
    "$1#packages.$(updates_system)" \
    --apply 'p: { gotg = p.gotg.outPath or null; ui = p."gotg-ui".outPath or null; }' 2>/dev/null || true
}

# A root that already is what the library would build is current, whatever
# its stamp says: the stamp is re-written so a launch does not run a build
# that would conclude the same thing (library_current).
updates_restamp() {
  local library="$1" cache attr want root stamp
  cache="$(updates_cache_file)"
  stamp="$(library_stamp "$library")"
  while IFS=$'\t' read -r attr want; do
    [[ -n "$attr" && -n "$want" && "$want" != null ]] || continue
    root="$(library_games_dir)/$attr"
    [[ -e "$root" && "$(readlink -f "$root")" == "$want" ]] || continue
    [[ "$(cat "$root.by" 2>/dev/null)" == "$stamp" ]] || printf '%s' "$stamp" >"$root.by"
  done < <(jq -r '.want // {} | to_entries[] | "\(.key)\t\(.value // "null")"' "$cache" 2>/dev/null)
}

# The work: ask upstream, evaluate, write the cache. Once per TTL unless
# --force; under a lock, since the picker's worker and a terminal may both
# ask. Offline keeps the last answer and says it is stale.
updates_check() {
  local force=0
  [[ "${1:-}" != "--force" ]] || force=1
  local library cache stamp now
  library="$(gotg_library)"
  [[ -n "$library" ]] || die "no library configured: set GOTG_LIBRARY, or \`library\` in $GOTG_CONFIG_FILE"
  cache="$(updates_cache_file)"
  stamp="$(library_stamp "$library")"
  now="$(date +%s)"
  if ((force == 0)) && [[ -s "$cache" ]]; then
    local at cstamp
    at="$(jq -r '.checked_at // 0' "$cache" 2>/dev/null || echo 0)"
    cstamp="$(jq -r '.stamp // ""' "$cache" 2>/dev/null || true)"
    if [[ "$cstamp" == "$stamp" ]] && ((now - at < ${GOTG_UPDATE_CHECK_TTL:-21600})); then
      return 0
    fi
  fi
  mkdir -p "$GOTG_STATE_DIR/locks"
  exec 9>"$GOTG_STATE_DIR/locks/updates.lock"
  flock 9

  local url="" latest="" stale=false
  url="$(updates_gotg_original "$library")"
  if [[ -n "$url" ]]; then
    latest="$(updates_latest_rev "$url")"
    if [[ -z "$latest" ]]; then
      stale=true
      latest="$(jq -r '.latest // empty' "$cache" 2>/dev/null || true)"
    fi
  fi
  local attrs=() want apps
  mapfile -t attrs < <(library_root_attrs)
  want="$(updates_eval_games "$library" "${attrs[@]}")"
  [[ -n "$want" ]] || want='{}'
  apps="$(updates_eval_apps "$library")"
  [[ -n "$apps" ]] || apps='{}'
  jq -n --arg stamp "$stamp" --argjson at "$now" --arg url "$url" --arg latest "$latest" \
    --argjson stale "$stale" --argjson want "$want" --argjson apps "$apps" \
    '{version: 1, stamp: $stamp, checked_at: $at, url: $url, latest: $latest, stale: $stale,
      want: $want, app_want: ($apps.gotg // null), ui_want: ($apps.ui // null)}' \
    >"$cache.tmp" && mv -f "$cache.tmp" "$cache"
  updates_restamp "$library"
  exec 9>&-
}

# One installed game's catalog row, by platform/id.
_updates_game() {
  local key="$1"
  jq -c --arg p "${key%%/*}" --arg i "${key#*/}" \
    'if .version != 2 then empty else (.games[] | select(.platform == $p and .id == $i)) end' \
    "$GOTG_CACHE_FILE" 2>/dev/null | head -1 || true
}

# The answer, from the cache and the filesystem only -- never the network,
# never an evaluation: the picker asks at startup and after every install,
# and a Deck should not feel it. See the top of this file for each field.
updates_json() {
  local library cache stamp writable=false pending=true stale=false
  local url="" latest="" locked="" behind=false unbuilt=false
  local app_want="" ui_want="" picker="" app_real="" ui_real=""
  library="$(gotg_library)"
  cache="$(updates_cache_file)"
  if [[ -n "$library" ]]; then
    stamp="$(library_stamp "$library")"
    locked="$(updates_gotg_locked "$library")"
    [[ -d "$library" && -w "$library" && "$library" != /nix/store/* ]] && writable=true
  fi
  if [[ -n "$library" && -s "$cache" ]]; then
    [[ "$(jq -r '.stamp // ""' "$cache" 2>/dev/null)" == "$stamp" ]] && pending=false
    url="$(jq -r '.url // ""' "$cache" 2>/dev/null)"
    latest="$(jq -r '.latest // ""' "$cache" 2>/dev/null)"
    stale="$(jq -r '.stale // false' "$cache" 2>/dev/null)"
    app_want="$(jq -r '.app_want // ""' "$cache" 2>/dev/null)"
    ui_want="$(jq -r '.ui_want // ""' "$cache" 2>/dev/null)"
  fi
  [[ -n "$latest" && -n "$locked" && "$latest" != "$locked" ]] && behind=true
  [[ -e "$GOTG_APP_ROOT" ]] && app_real="$(readlink -f "$GOTG_APP_ROOT")"
  [[ -e "$GOTG_UI_ROOT" ]] && ui_real="$(readlink -f "$GOTG_UI_ROOT")"
  picker="$ui_real"
  if [[ "$pending" == false ]]; then
    [[ -n "$app_want" && "$app_real" != "$app_want" ]] && unbuilt=true
    [[ -n "$ui_want" && "$ui_real" != "$ui_want" ]] && unbuilt=true
  fi

  # Games: a root that is not what the lock would build, and an install
  # missing an attached release. Keyed by platform/id, the variants' roots
  # gathered under the game.
  local -A reasons=() attrs=()
  local attr want root key
  if [[ "$pending" == false ]]; then
    while IFS=$'\t' read -r attr want; do
      [[ -n "$attr" && -n "$want" && "$want" != null ]] || continue
      root="$(library_games_dir)/$attr"
      [[ -e "$root" ]] || continue
      [[ "$(readlink -f "$root")" != "$want" ]] || continue
      key="$(updates_attr_key "$attr")"
      attrs["$key"]+="${attrs[$key]:+ }$attr"
      [[ " ${reasons[$key]:-} " == *" build "* ]] || reasons["$key"]+="${reasons[$key]:+ }build"
    done < <(jq -r '.want // {} | to_entries[] | "\(.key)\t\(.value // "null")"' "$cache" 2>/dev/null)
  fi
  local game install
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    game="$(_updates_game "$key")"
    [[ -n "$game" ]] || continue
    game_has_extras "$game" || continue
    install="$(game_installed_path "$game")" || continue
    [[ -d "$install" ]] || continue
    [[ -n "$(_extras_missing "$install" "$game")" ]] || continue
    attrs["$key"]="${attrs[$key]:-}"
    reasons["$key"]+="${reasons[$key]:+ }extras"
  done < <(manifest_installed_keys)

  local games='[]' entry
  for key in "${!reasons[@]}"; do
    entry="$(jq -nc --arg k "$key" --arg a "${attrs[$key]:-}" --arg r "${reasons[$key]}" \
      '{key: $k, attrs: ($a | split(" ") | map(select(length > 0))), reasons: ($r | split(" "))}')"
    games="$(jq -c --argjson e "$entry" '. + [$e]' <<<"$games")"
  done
  jq -n --argjson pending "$pending" --argjson stale "$stale" --arg library "$library" \
    --argjson writable "$writable" --arg url "$url" --arg locked "$locked" --arg latest "$latest" \
    --argjson behind "$behind" --argjson unbuilt "$unbuilt" --arg picker "$picker" --argjson games "$games" \
    --argjson at "$(jq -r '.checked_at // 0' "$cache" 2>/dev/null || echo 0)" \
    '{version: 1, checked_at: $at, stale: $stale, pending: $pending, library: $library, writable: $writable,
      gotg: {url: $url, locked: $locked, latest: $latest, behind: $behind, unbuilt: $unbuilt,
             available: ($behind or $unbuilt), picker: (if $picker == "" then null else $picker end)},
      games: ($games | sort_by(.key))}'
}
