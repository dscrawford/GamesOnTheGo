# shellcheck shell=bash
# Console firmware, installed before the emulator can stop to ask for it.
#
# Ryujinx will not launch a game without firmware, and there is no setting to
# skip the dialog it raises instead — pre-installing is the only way the first
# launch goes straight to the game. The GUI installer's whole effect on disk is
# a directory per NCA under Contents/registered, holding the raw file as `00`,
# so unpacking the same zip into that shape is indistinguishable from having
# clicked through it. No version file exists: the emulator rescans the
# directory and reads the version out of one NCA, using the keys that keys.sh
# already fetched.
#
# Like the keys, firmware is not ours to ship — it belongs to a console — so it
# lives beside them on the server, placed by hand, and is fetched on demand.
# Unlike the keys it is hundreds of megabytes, so it is fetched once into a
# per-platform cache and hardlinked into each environment that needs it, and an
# environment that already has firmware can seed the cache instead of the
# server being asked at all.

firmware_manifest() { printf '%s/%s/share/gotg/firmware.json' "$GOTG_ROOTS_DIR" "$1"; }

firmware_cache_dir() { printf '%s/%s' "${GOTG_FIRMWARE_DIR:-$GOTG_STATE_DIR/firmware}" "$1"; }

# Caps both the download and what the zip claims it inflates to. Real firmware
# is a few hundred megabytes; anything past this is not firmware, whatever the
# server says it is.
firmware_max_bytes() { printf '%s' "${GOTG_FIRMWARE_MAX_BYTES:-2147483648}"; }

# A glob rather than ls: no fork and no capturing a 229-entry listing, on a
# path every single launch checks. (compgen would read better, but the
# non-interactive bash nix builds has no completion builtins at all.)
firmware_populated() {
  local entry
  for entry in "$1"/*; do
    [[ -e "$entry" ]] && return 0
  done
  return 1
}

# Unpack a firmware zip into the layout the emulator installs for itself:
# registered/<name>.nca/00, with `.cnmt` stripped from the directory name, the
# same rename its own installer performs. Flat `<name>.nca` files inside
# `registered/` are ignored by the emulator's scan, which is why the directory
# shape is not optional. Handles a zip of flat NCAs and one already shaped as
# <name>.nca/00, since both exist in the wild.
#
# Staging lives beside the destination under fixed names, for two reasons: the
# same filesystem makes every move below a rename rather than a copy — and
# never a write into a tmpfs /tmp — and an interrupted run's leftovers are
# swept by the next run instead of accumulating under names nothing ever
# revisits. Fixed names are safe because every caller holds the platform lock.
#
#   $1 the zip   $2 the registered directory to (re)create
firmware_unpack() {
  local zip="$1" reg="$2" tmp entry name
  tmp="$reg.unpack"
  rm -rf "$tmp" "$reg.part"
  mkdir -p "$tmp" "$reg.part" || return 1

  # Refuse a bomb before inflating it: the entries' declared sizes are known
  # up front. python3 is already on gotg's PATH for the Steam library; its
  # extraction also contains hostile entries — `..` is dropped, a leading `/`
  # stripped, a symlink written as a plain file — which an `unzip` here would
  # not. Verified against a crafted archive, not assumed.
  if ! python3 - "$zip" "$(firmware_max_bytes)" <<'EOF'
import sys, zipfile
try:
    total = sum(i.file_size for i in zipfile.ZipFile(sys.argv[1]).infolist())
except Exception:
    sys.exit(1)
sys.exit(0 if total <= int(sys.argv[2]) else 1)
EOF
  then
    rm -rf "$tmp" "$reg.part"
    return 1
  fi

  if ! python3 -m zipfile -e "$zip" "$tmp/" 2>/dev/null; then
    rm -rf "$tmp" "$reg.part"
    return 1
  fi

  # Any single failure fails the whole install: a partial set that passed the
  # populated check would be cached, linked everywhere, and never retried.
  while IFS= read -r -d '' entry; do
    case "$entry" in
      */00) name="${entry%/00}" name="${name##*/}" ;;
      *) name="${entry##*/}" ;;
    esac
    name="${name//.cnmt/}"
    if ! mkdir -p "$reg.part/$name" || ! mv "$entry" "$reg.part/$name/00"; then
      rm -rf "$tmp" "$reg.part"
      return 1
    fi
  done < <(find "$tmp" -type f \( -name '*.nca' -o -path '*.nca/00' \) -print0)
  rm -rf "$tmp"

  # A zip with no NCAs in it is not firmware, whatever it is named.
  firmware_populated "$reg.part" || {
    rm -rf "$reg.part"
    return 1
  }
  rm -rf "$reg"
  mv "$reg.part" "$reg"
}

# One registered tree placed at another path, hardlinked where the filesystem
# allows so that every environment shares one copy of a 300MB set. The NCAs are
# never edited in place — a reinstall in the emulator's own UI deletes the
# directory and rebuilds it, which breaks the links rather than writing through
# them — so sharing inodes is safe.
firmware_link() {
  local src="$1" dst="$2"
  rm -rf "$dst.part"
  mkdir -p "$(dirname "$dst")"
  if ! cp -al "$src" "$dst.part" 2>/dev/null; then
    rm -rf "$dst.part"
    cp -a --no-preserve=mode "$src" "$dst.part" || {
      rm -rf "$dst.part"
      return 1
    }
  fi
  rm -rf "$dst"
  mv "$dst.part" "$dst"
}

# Seed the cache from any environment that already has firmware — the machines
# that installed it through the emulator's dialog before this existed. Their
# copy is byte-identical to anything the server would hand out.
firmware_adopt() {
  local cache="$1" into="$2" dir
  for dir in "${GOTG_ENV_STATE_DIR:-$GOTG_STATE_DIR/env}"/*/"$into"; do
    firmware_populated "$dir" || continue
    firmware_link "$dir" "$cache" && return 0
  done
  return 1
}

# The server's copy, unpacked straight into the cache.
firmware_fetch() {
  local cache="$1" platform="$2" file="$3" zip rc=0
  service_have || return 1

  zip="$cache.zip"
  rm -f "$zip"
  # A firmware zip is a few hundred megabytes on hotel Wi-Fi, so the timeout
  # is its own; the size cap is the other half of not trusting the far end —
  # see firmware_unpack for the decompressed half.
  if ! service_curl -fsS \
    --max-time "${GOTG_FIRMWARE_FETCH_SECONDS:-900}" \
    --max-filesize "$(firmware_max_bytes)" \
    "$(service_url)/files/$platform/$(jq -rn --arg n "$file" '$n | @uri')" >"$zip" 2>/dev/null ||
    [[ ! -s "$zip" ]]; then
    rm -f "$zip"
    return 1
  fi

  log "fetched $file"
  firmware_unpack "$zip" "$cache" || rc=1
  rm -f "$zip"
  return "$rc"
}

# Put firmware where this environment's emulator looks for it, if the
# environment declares a place. Never fatal: a launch without firmware still
# reaches the emulator's own dialog, which can install from an XCI's update
# partition — something the server may not have.
#
#   $1 environment attribute   $2 the platform whose directory holds the zip
firmware_ensure() {
  local attr="$1" platform="$2" manifest spec into file dest cache

  manifest="$(firmware_manifest "$attr")"
  [[ -f "$manifest" ]] || return 0
  spec="$(jq -r '[.into // "", .file // ""] | @tsv' "$manifest" 2>/dev/null)" || spec=""
  into="${spec%%$'\t'*}"
  file="${spec#*$'\t'}"
  [[ -n "$into" && -n "$file" ]] || return 0

  # Joined onto paths that later meet rm -rf. Today it only ever comes out of
  # the nix store, so this keeps that a fact rather than an assumption.
  if [[ "$into" == /* || "$into" == *..* ]]; then
    warn "refusing firmware path from $attr: $into"
    return 0
  fi

  dest="$(env_state_dir "$attr")/$into"
  firmware_populated "$dest" && return 0

  cache="$(firmware_cache_dir "$platform")"
  mkdir -p "$(dirname "$cache")" || return 0

  # One launch does the filling; a racer waits at the lock and finds it done.
  # Without this, two first launches interleave their fixed staging paths and
  # can publish each other's half — a partial cache that looks populated and
  # is therefore never retried.
  if ! (
    flock 9
    if ! firmware_populated "$cache"; then
      firmware_adopt "$cache" "$into" ||
        firmware_fetch "$cache" "$platform" "$file" ||
        exit 1
    fi
    firmware_link "$cache" "$dest"
  ) 9>"$cache.lock"; then
    if firmware_populated "$cache"; then
      warn "could not place firmware for $attr — the emulator will ask instead"
    else
      warn "no $platform firmware here or on the server at /Games/$platform/$file"
      warn "the emulator will offer to install some on the first launch"
    fi
  fi
  return 0
}
