# shellcheck shell=bash
# Which versions of a game are here, and which one a launch runs.
#
# A Switch game is a base and a pile of updates, and the update decides what
# the game *is*: mods are built against one version's executable, and an exefs
# mod on the wrong one either does nothing or crashes a minute in. Tears of the
# Kingdom is the reason this exists — UltraCam supports up to 1.4.2, the
# library carried 1.4.3, and the failure was a null dereference inside the mod
# with nothing on screen to say why.
#
# So a version becomes something to choose: `gotg play <id> --version 1.4.2`,
# a row in the picker's menu, or a ceiling a mod states for itself and the
# client respects without being asked.
#
# Read from the file names rather than the containers. The importer names an
# update for its version — extras/update_1.4.3-… — and reading a name costs
# nothing where opening the NCA costs keys, a python and a second of every
# launch. The env's own content.py still does the authoritative matching when
# it registers the choice with the emulator.

# extras/update_<version>-<whatever>.nsp, which is what the importer writes.
GOTG_UPDATE_RE='^update_([0-9]+(\.[0-9]+)*)[-_.]'

# Every version installed for one game, newest first, as "<version>\t<path>".
# The base game is not listed: it is what you get by choosing none of these.
versions_available() {
  local game="$1" install extras file name version
  install="$(game_installed_path "$game")" || return 0
  extras="$install/extras"
  [[ -d "$extras" ]] || return 0

  local rows=()
  for file in "$extras"/*; do
    [[ -f "$file" ]] || continue
    name="$(basename "$file")"
    [[ "$name" =~ $GOTG_UPDATE_RE ]] || continue
    version="${BASH_REMATCH[1]}"
    rows+=("$version	$file")
  done
  ((${#rows[@]} > 0)) || return 0
  # Newest first, by version rather than by string: 1.4.10 is above 1.4.9.
  printf '%s\n' "${rows[@]}" | sort -rV -t'	' -k1,1
}

versions_names() { versions_available "$1" | cut -f1; }

# The newest version installed, which is what a launch uses when nothing says
# otherwise — and what the emulator would have picked for itself.
versions_newest() { versions_names "$1" | head -n1; }

# Whether this game has a choice to offer at all.
versions_many() { [[ "$(versions_names "$1" | wc -l)" -gt 1 ]]; }

# Is this one of the versions installed?
versions_has() {
  local game="$1" want="$2" have
  while IFS= read -r have; do
    [[ "$have" == "$want" ]] && return 0
  done < <(versions_names "$game")
  return 1
}

# The newest version at or below a ceiling, or nothing.
#
# A mod states the newest version it was built against; this is what the client
# does about it. Compared with sort -V so 1.4.10 is above 1.4.9 and below 1.5.
versions_at_most() {
  local game="$1" ceiling="$2" version
  while IFS= read -r version; do
    [[ -n "$version" ]] || continue
    if [[ "$(printf '%s\n%s\n' "$version" "$ceiling" | sort -V | head -n1)" == "$version" ]]; then
      printf '%s' "$version"
      return 0
    fi
  done < <(versions_names "$game")
  return 1
}

# What an environment says about the game version it was built for, read off
# its built manifest like everything else on this path — never a nix
# evaluation. Empty when it says nothing, which is every environment that is
# not a version-specific mod.
versions_env_ceiling() {
  local attr="$1" manifest
  manifest="$GOTG_ROOTS_DIR/$attr/share/gotg/saves.json"
  [[ -f "$manifest" ]] || return 0
  jq -r '.gameVersionMax // empty' "$manifest" 2>/dev/null
}

# The version a launch should run: what was asked for, what the mod can take,
# or the newest. Prints it; prints nothing when the game has no updates at all.
#
# Dies rather than guesses when an explicit request cannot be honoured — a
# launch that silently ran a different version than the one asked for is how
# somebody spends an evening wondering why a mod does nothing.
versions_resolve() {
  local game="$1" attr="$2" want="${3:-}" ceiling newest fit
  newest="$(versions_newest "$game")"
  [[ -n "$newest" ]] || return 0

  if [[ -n "$want" ]]; then
    versions_has "$game" "$want" ||
      die "no version $want of $(manifest_field "$game" title) here.
     Installed: $(versions_names "$game" | tr '\n' ' ')"
    printf '%s' "$want"
    return 0
  fi

  ceiling="$(versions_env_ceiling "$attr")"
  [[ -n "$ceiling" ]] || { printf '%s' "$newest"; return 0; }

  if fit="$(versions_at_most "$game" "$ceiling")"; then
    [[ "$fit" == "$newest" ]] ||
      warn "$attr is built for $ceiling or older; running $fit rather than $newest"
    printf '%s' "$fit"
    return 0
  fi
  die "$attr needs version $ceiling or older, and the oldest here is $(versions_names "$game" | tail -n1).
     The mod would load, patch nothing, and the game would crash a minute in —
     so this refuses rather than letting that happen.
     Add an older update to the library, or play without the mod."
}

# --- the command ------------------------------------------------------------

cmd_versions() {
  local want="${1:-}" variant="${2:-}"
  [[ -n "$want" ]] || die "usage: gotg versions <id> [variant]"
  manifest_cached || manifest_ensure

  local game attr names newest ceiling running
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game" "$variant")"
  mapfile -t names < <(versions_names "$game")
  if ((${#names[@]} == 0)); then
    log "$(manifest_field "$game" title) has no updates here — the base game is all there is."
    return 0
  fi

  newest="$(versions_newest "$game")"
  ceiling="$(versions_env_ceiling "$attr")"
  # The `|| true` belongs out here: versions_resolve dies when a mod cannot
  # take any version installed, and an exit inside $( ) never reaches an || on
  # its inside — it kills the subshell, and the status falls out to the
  # assignment. Listing what is here is exactly the moment somebody wants to
  # see, not be stopped by, that refusal.
  running="$(versions_resolve "$game" "$attr" 2>/dev/null)" || running=""

  local version mark
  for version in "${names[@]}"; do
    mark="  "
    [[ "$version" == "$running" ]] && mark="* "
    if [[ "$version" == "$newest" ]]; then
      printf '%s%s (newest)\n' "$mark" "$version"
    else
      printf '%s%s\n' "$mark" "$version"
    fi
  done
  [[ -z "$ceiling" ]] ||
    log "${C_DIM}$attr is built for $ceiling or older${C_RESET}"
  log "${C_DIM}* is what a launch runs; gotg play $want${variant:+ $variant} --version <version> picks another${C_RESET}"
}
