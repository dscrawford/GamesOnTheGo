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
# a row in the picker's menu, or a window a mod states for itself and the
# client respects without being asked. A window and not a ceiling, because a
# patch can be too new for a dump as easily as too old for one: UltraCam's
# shipped subsdk3 hooks Tears of the Kingdom 1.1.0 through 1.4.2, and on 1.0.0
# it finds nothing to hook exactly as it does on 1.4.3.
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

# Is the first version no newer than the second? Compared with sort -V, so
# 1.4.10 is above 1.4.9 and below 1.5, which no string comparison gets right.
versions_le() {
  [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$1" ]]
}

# Is one version inside a mod's window? An empty end is an open one.
versions_inside() {
  local version="$1" floor="$2" ceiling="$3"
  [[ -z "$ceiling" ]] || versions_le "$version" "$ceiling" || return 1
  [[ -z "$floor" ]] || versions_le "$floor" "$version" || return 1
  return 0
}

# The newest version installed inside a mod's window, or nothing.
versions_within() {
  local game="$1" floor="$2" ceiling="$3" version
  while IFS= read -r version; do
    [[ -n "$version" ]] || continue
    if versions_inside "$version" "$floor" "$ceiling"; then
      printf '%s' "$version"
      return 0
    fi
  done < <(versions_names "$game")
  return 1
}

# The newest version at or below a ceiling, or nothing — the open-floored case
# of the above, kept because a ceiling alone is what most mods state.
versions_at_most() { versions_within "$1" "" "$2"; }

# A window in the words an error message wants: "1.4.2 or older", "1.6.0 or
# newer", "1.1.0 to 1.4.2", "exactly 1.6.0". Empty when a mod states neither
# end, which is every environment that is not version-specific.
versions_window_words() {
  local floor="$1" ceiling="$2"
  if [[ -n "$floor" && -n "$ceiling" ]]; then
    if [[ "$floor" == "$ceiling" ]]; then
      printf 'exactly %s' "$floor"
    else
      printf '%s to %s' "$floor" "$ceiling"
    fi
  elif [[ -n "$ceiling" ]]; then
    printf '%s or older' "$ceiling"
  elif [[ -n "$floor" ]]; then
    printf '%s or newer' "$floor"
  fi
}

# What an environment says about the game versions it was built for, read off
# its built manifest like everything else on this path — never a nix
# evaluation. Empty when it says nothing, which is every environment that is
# not a version-specific mod.
#
# Unbuilt is unknown, not unbounded: there is no manifest to read until the
# environment exists, so a variant nobody has built yet states no window and is
# treated as able to run. It is judged the moment it is built, which is also
# the first moment it could have been wrong.
versions_env_ceiling() { versions_env_field "$1" gameVersionMax; }
versions_env_floor() { versions_env_field "$1" gameVersionMin; }

versions_env_field() {
  local attr="$1" field="$2" manifest
  manifest="$GOTG_ROOTS_DIR/$attr/share/gotg/saves.json"
  [[ -f "$manifest" ]] || return 0
  jq -r --arg f "$field" '.[$f] // empty' "$manifest" 2>/dev/null
}

# Both ends at once, one per line — and read with `mapfile` rather than packed
# into one line and split. A tab-separated pair looks like the obvious thing
# here and is a trap: tab is IFS whitespace, so `read -r floor ceiling` on a
# line that begins with one *skips* the empty first field and puts the ceiling
# in the floor. Which is a mod with a ceiling of 1.4.2 quietly becoming a mod
# that demands 1.4.2 or newer.
versions_env_window() {
  printf '%s\n%s\n' "$(versions_env_floor "$1")" "$(versions_env_ceiling "$1")"
}

# The version a launch should run: what was asked for, what the mod can take,
# or the newest. Prints it; prints nothing when the game has no updates at all.
#
# Dies rather than guesses when an explicit request cannot be honoured — a
# launch that silently ran a different version than the one asked for is how
# somebody spends an evening wondering why a mod does nothing.
versions_resolve() {
  local game="$1" attr="$2" want="${3:-}" floor ceiling words newest fit
  local -a window
  newest="$(versions_newest "$game")"
  [[ -n "$newest" ]] || return 0

  mapfile -t window < <(versions_env_window "$attr")
  floor="${window[0]}"
  ceiling="${window[1]}"
  words="$(versions_window_words "$floor" "$ceiling")"

  if [[ -n "$want" ]]; then
    versions_has "$game" "$want" ||
      die "no version $want of $(manifest_field "$game" title) here.
     Installed: $(versions_names "$game" | tr '\n' ' ')"
    # An explicit ask is still an ask of a particular mod. Honouring it against
    # a version the mod cannot patch would hand back exactly the crash this
    # whole file exists to prevent, and hand it back on request.
    versions_inside "$want" "$floor" "$ceiling" ||
      die "$attr is built for $words, and $want is not.
     Run it without the mod, or pick a version it can take:
       gotg versions $(manifest_field "$game" id)"
    printf '%s' "$want"
    return 0
  fi

  [[ -n "$words" ]] || { printf '%s' "$newest"; return 0; }

  if fit="$(versions_within "$game" "$floor" "$ceiling")"; then
    [[ "$fit" == "$newest" ]] ||
      warn "$attr is built for $words; running $fit rather than $newest"
    printf '%s' "$fit"
    return 0
  fi
  die "$attr needs version $words, and what is here is $(versions_names "$game" | tr '\n' ' ').
     The mod would load, patch nothing, and the game would crash a minute in —
     so this refuses rather than letting that happen.
     Add an update it can take to the library, or play without the mod."
}

# --- a mod a version window rules out ---------------------------------------

# Whether this environment can run at all on what is installed.
#
# A mod that states a window and finds nothing inside it is not broken, it is
# *disabled*: there is no launch it could do and nothing the player can press
# to fix it. So it stops being offered — in completion, in the picker's menu —
# rather than sitting there as a row that answers with a paragraph.
#
# Judged only for a game that is here. An uninstalled game has no updates to
# read yet, and hiding a mod of it would be guessing at bytes that have not
# arrived.
versions_variant_runnable() {
  local game="$1" attr="$2" floor ceiling
  local -a window
  mapfile -t window < <(versions_env_window "$attr")
  floor="${window[0]}"
  ceiling="${window[1]}"
  [[ -n "$floor$ceiling" ]] || return 0
  game_is_installed "$game" || return 0

  # No updates installed at all means the base game, which no `update_` file
  # names: too old for a floor, and under any ceiling.
  if [[ -z "$(versions_newest "$game")" ]]; then
    [[ -z "$floor" ]]
    return
  fi
  versions_within "$game" "$floor" "$ceiling" >/dev/null
}

# Why it cannot, in a few words — for the one place that says so out loud.
versions_variant_why() {
  local attr="$1" floor ceiling
  local -a window
  mapfile -t window < <(versions_env_window "$attr")
  floor="${window[0]}"
  ceiling="${window[1]}"
  printf 'needs %s' "$(versions_window_words "$floor" "$ceiling")"
}

# One game's variants, split by whether they can run. Both walk the same list
# so the two answers cannot drift apart.
versions_variants_runnable() { _versions_variants "$1" runnable; }
versions_variants_disabled() { _versions_variants "$1" disabled; }

_versions_variants() {
  local game="$1" which="$2" platform id name attr
  platform="$(manifest_field "$game" platform)"
  id="$(manifest_field "$game" id)"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    attr="$(env_attr "$game" "$name" 2>/dev/null)" || continue
    if versions_variant_runnable "$game" "$attr"; then
      [[ "$which" == runnable ]] && printf '%s\n' "$name"
    else
      [[ "$which" == disabled ]] && printf '%s\n' "$name"
    fi
  done < <(env_variant_names "$platform" "$id")
  return 0
}

# --- the command ------------------------------------------------------------

cmd_versions() {
  local want="${1:-}" variant="${2:-}"
  [[ -n "$want" ]] || die "usage: gotg versions <id> [variant]"
  manifest_cached || manifest_ensure

  local game attr names newest floor ceiling words running
  local -a window
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game" "$variant")"
  mapfile -t names < <(versions_names "$game")
  if ((${#names[@]} == 0)); then
    log "$(manifest_field "$game" title) has no updates here — the base game is all there is."
    return 0
  fi

  newest="$(versions_newest "$game")"
  mapfile -t window < <(versions_env_window "$attr")
  floor="${window[0]}"
  ceiling="${window[1]}"
  words="$(versions_window_words "$floor" "$ceiling")"
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
  [[ -z "$words" ]] ||
    log "${C_DIM}$attr is built for $words${C_RESET}"
  log "${C_DIM}* is what a launch runs; gotg play $want${variant:+ $variant} --version <version> picks another${C_RESET}"
}
