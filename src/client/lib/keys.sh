# shellcheck shell=bash
# Console keys and other files an emulator needs that are not the game.
#
# A Switch game will not decrypt without prod.keys, so a launch without them
# fails inside the emulator with an error about the game rather than about the
# keys — which is a bad way to find out. These are fetched before the emulator
# starts, once, and reported plainly when they are missing.
#
# They live beside the games on the server, in the platform's own directory, and
# are deliberately not in the catalog: the manifest is built from what the
# importer imported, and a key file is placed by hand. See IMPORTER_SPEC.md §11.
#
# Kept out of the nix store on purpose. Keys belong to a console, are not
# redistributable, and track firmware — so a store path holding them would be
# both wrong and stale, and world-readable in the bargain.

keys_manifest() { printf '%s/%s/share/gotg/keys.json' "$GOTG_ROOTS_DIR" "$1"; }

# Fetch whatever this environment declares it needs, into the environment's own
# state directory. Missing keys are a warning and not a failure: the emulator is
# still worth starting, since it says more about a specific game than we can.
#
# One key file, from whichever byte host answers first.
#
#   $1 platform   $2 file name   $3 where to write it
keys_fetch() {
  local platform="$1" file="$2" dest="$3" host
  while IFS= read -r host; do
    [[ -n "$host" ]] || continue
    if service_curl -fsS --max-time "${GOTG_API_TIMEOUT:-120}" \
      "$host/files/$platform/$(jq -rn --arg n "$file" '$n | @uri')" \
      >"$dest" 2>/dev/null && [[ -s "$dest" ]]; then
      return 0
    fi
    rm -f "$dest"
  done < <(manifest_files_hosts)
  return 1
}

#   $1 environment attribute   $2 the platform whose directory holds them
keys_ensure() {
  local attr="$1" platform="$2" manifest into state file dest missing=0

  manifest="$(keys_manifest "$attr")"
  [[ -f "$manifest" ]] || return 0

  # Usually the game's own platform, but not always: Four Swords Adventures is
  # a GameCube game that will not emulate its Game Boy Advances without the
  # GBA's BIOS, and that file belongs to the Game Boy Advance directory rather
  # than being copied into every platform that has a use for it.
  local from
  from="$(jq -r '.platform // empty' "$manifest")"
  [[ -n "$from" ]] && platform="$from"

  into="$(jq -r '.into // empty' "$manifest")"
  [[ -n "$into" ]] || return 0
  if [[ "$into" == /* || "$into" == *..* ]]; then
    warn "refusing keys path from $attr: $into"
    return 0
  fi

  state="$(env_state_dir "$attr")"
  local dir="$state/$into"

  # Nothing to do is the common case, so the server is not contacted at all
  # unless something is actually absent.
  local wanted=() have_all=1
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    wanted+=("$file")
    [[ -s "$dir/$file" ]] || have_all=0
  done < <(jq -r '.files[]? // empty' "$manifest")
  ((${#wanted[@]} > 0)) || return 0
  ((have_all == 0)) || return 0

  mkdir -p "$dir" || return 0
  service_have || {
    warn "no service configured for $platform keys — run: gotg login"
    return 0
  }

  for file in "${wanted[@]}"; do
    dest="$dir/$file"
    [[ -s "$dest" ]] && continue

    # Written to a temporary name and moved, so an interrupted fetch cannot
    # leave a half a key file looking like a whole one.
    if [[ "$file" == /* || "$file" == *..* || "$file" == */* ]]; then
      warn "refusing key file name: $file"
      missing=$((missing + 1))
      continue
    fi
    # From the byte host, in the catalog's order of preference, exactly like a
    # game download: /files lives beside /games, off the proxied control plane,
    # and asking the control plane for it is a 503 from a service that is
    # working perfectly.
    if keys_fetch "$platform" "$file" "$dest.part" &&
      [[ -s "$dest.part" ]]; then
      chmod 600 "$dest.part"
      mv "$dest.part" "$dest"
      log "fetched $file"
    else
      rm -f "$dest.part"
      missing=$((missing + 1))
      warn "no $file on the server at /Games/$platform/$file"
    fi
  done

  if ((missing > 0)); then
    warn "$platform needs its keys in /Games/$platform — see IMPORTER_SPEC.md"
  fi
}
