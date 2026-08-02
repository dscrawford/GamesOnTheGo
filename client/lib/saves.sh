# shellcheck shell=bash
# Saves: what to bundle, what a generation is, and which side wins.
#
# The unit of sync is the environment, not the game. env-snes is shared by every
# SNES title and its state directory holds all their memory saves at once;
# splitting them per game would need a second source of truth about which file
# belongs to which title, and getting that wrong loses saves. A game id is
# accepted and resolved the same way `play` resolves it, and then the commands
# say plainly which environment they are actually working on.
#
# One zstd tar per environment rather than a file at a time. A save set is only
# meaningful whole — Dolphin's NAND tree, ares' memory save beside its state —
# and per-file transfer lets two machines interleave halves of two saves. It is
# also one sha256 over the tar that *is* the generation's identity and the
# entire conflict detector.

# One temporary directory per process, cleaned up however the command exits.
#
# Created by the command rather than on first use: everything here reaches for
# it as "$(saves_tmp)", and making it there would put both the directory and its
# cleanup trap inside a command substitution — so the subshell would delete the
# directory on its way out and hand back a path to nothing.
SAVES_TMP=""

saves_tmp_init() {
  [[ -n "$SAVES_TMP" ]] && return 0
  SAVES_TMP="$(mktemp -d "${TMPDIR:-/tmp}/gotg-saves.XXXXXX")"
  # shellcheck disable=SC2064 # expand the path now, not at exit
  trap "rm -rf '$SAVES_TMP'" EXIT
}

saves_tmp() { printf '%s' "$SAVES_TMP"; }

saves_hash() { sha256sum "$1" | cut -d' ' -f1; }

# This machine, as it appears in the metadata a person reads when choosing
# between two saves. Not the hostname: hostnames repeat, and "steamdeck" is not
# unique to one Steam Deck.
device_id() {
  local file="$GOTG_CONFIG_DIR/device"
  if [[ -n "${GOTG_DEVICE_ID:-}" ]]; then
    printf '%s' "$GOTG_DEVICE_ID"
    return 0
  fi
  if [[ ! -f "$file" ]]; then
    mkdir -p "$GOTG_CONFIG_DIR"
    chmod 700 "$GOTG_CONFIG_DIR"
    od -An -tx1 -N4 /dev/urandom | tr -d ' \n' >"$file"
  fi
  cat "$file"
}

# ---------------------------------------------------------------- the journal

# What this machine believes about the remote: the generation it descends from,
# and the last thing it uploaded. base_generation and base_hash are the whole
# conflict model — everything else is presentation.
saves_journal_path() {
  validate_attr "$1"
  printf '%s/%s.json' "$GOTG_SAVES_DIR" "$1"
}

saves_journal_get() {
  local file
  file="$(saves_journal_path "$1")"
  [[ -f "$file" ]] || return 0
  jq -r --arg f "$2" '.[$f] // empty' "$file"
}

saves_journal_set() {
  local file
  file="$(saves_journal_path "$1")"
  mkdir -p "$GOTG_SAVES_DIR"
  local existing='{}'
  [[ -f "$file" ]] && existing="$(cat "$file")"
  jq -n --argjson existing "$existing" --argjson patch "$2" '$existing + $patch' >"$file.tmp"
  mv "$file.tmp" "$file"
}

# --------------------------------------------------------------- the manifest

saves_manifest() {
  local attr="$1" file
  file="$(env_saves_manifest "$attr")"
  [[ -f "$file" ]] || die "$attr is not built here, so there is nothing that knows what its saves are.
     Build it with: gotg install <id>"
  cat "$file"
}

# The files a bundle would hold, relative to the state directory. Regular files
# only: directories are recreated by extraction, and a symlink is something we
# never write and never accept.
saves_member_list() {
  local state="$1"
  shift
  (
    cd "$state" 2>/dev/null || return 0
    shopt -s globstar nullglob dotglob
    local glob match
    for glob in "$@"; do
      # shellcheck disable=SC2086 # the manifest entry is a glob on purpose
      for match in $glob; do
        [[ -f "$match" && ! -L "$match" ]] && printf '%s\n' "$match"
      done
    done
  ) | LC_ALL=C sort -u
}

# Refuse to bundle something enormous, naming what made it enormous. This is
# the guard that catches a glob which has quietly started matching a .o2r or a
# disc image, and it is worth more than the exclude list because it does not
# have to be complete in order to work.
saves_check_size() {
  local state="$1"
  shift
  local total=0 size file
  for file in "$@"; do
    size="$(stat -c '%s' "$state/$file" 2>/dev/null || echo 0)"
    total=$((total + size))
  done

  local max
  max="$(saves_max_bytes)"
  if ((total > max)); then
    local biggest
    biggest="$(for file in "$@"; do
      printf '%s\t%s\n' "$(stat -c '%s' "$state/$file" 2>/dev/null || echo 0)" "$file"
    done | sort -rn | head -5 | while IFS=$'\t' read -r size file; do
      printf '       %10s  %s\n' "$(human_size "$size")" "$file"
    done)"
    die "the saves for this environment come to $(human_size "$total"), over the $(human_size "$max") limit.
     Nothing was uploaded. The largest are:
$biggest
     If one of those is not a save, exclude it in client/env; if they are all
     real, raise GOTG_SAVES_MAX_BYTES."
  fi
}

# Build the bundle. Returns 1 — not an error — when there is nothing to bundle.
#
# Deterministic on purpose: same files in, same bytes out, so "has anything
# changed?" is a hash comparison rather than a diff, and a push from a machine
# that has changed nothing uploads nothing. None of these emulators order saves
# by timestamp, so zeroing them costs nothing.
saves_bundle() {
  local attr="$1" out="$2" manifest state
  manifest="$(saves_manifest "$attr")"
  state="$(env_state_dir "$attr")"
  [[ -d "$state" ]] || return 1

  local globs=() excludes=() members=()
  mapfile -t globs < <(jq -r '.saves[]?' <<<"$manifest")
  mapfile -t excludes < <(jq -r '.excludes[]?' <<<"$manifest")
  [[ ${#globs[@]} -gt 0 ]] || return 1

  mapfile -t members < <(saves_member_list "$state" "${globs[@]}")
  [[ ${#members[@]} -gt 0 ]] || return 1

  saves_check_size "$state" "${members[@]}"

  local exclude_args=() pattern
  for pattern in "${excludes[@]}"; do
    exclude_args+=("--exclude=$pattern")
  done

  # A tar that fails must not look like "there was nothing to send" — that is
  # the difference between a quiet no-op and a save that never left the machine.
  tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    --format=pax --pax-option='exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime' \
    "${exclude_args[@]}" \
    --zstd -C "$state" -cf "$out" -- "${members[@]}" ||
    die "could not bundle the saves for $attr"
}

saves_member_count() {
  local attr="$1" manifest state globs=()
  manifest="$(saves_manifest "$attr")"
  state="$(env_state_dir "$attr")"
  mapfile -t globs < <(jq -r '.saves[]?' <<<"$manifest")
  [[ ${#globs[@]} -gt 0 ]] || {
    printf '0'
    return 0
  }
  saves_member_list "$state" "${globs[@]}" | grep -c . || true
}

# ------------------------------------------------------------------ the remote

# The pointer every fast path reads: one GET decides whether there is anything
# to do at all. Returns 1 when nothing has ever been pushed.
saves_latest() {
  local attr="$1" tmp file
  tmp="$(saves_tmp)"
  file="$tmp/latest-$attr.json"
  blob_get "$attr/latest.json" "$file" || return 1

  jq -e '.version == 1
         and (.generation | type == "number")
         and (.hash | type == "string")
         and (.bundle | type == "string")' >/dev/null 2>&1 <"$file" ||
    die "$attr/latest.json on the remote is not something this version understands"

  # The bundle name is about to become a URL and a path, and it came from the
  # remote, so it is checked before it is used rather than after.
  local bundle
  bundle="$(jq -r '.bundle' <"$file")"
  validate_blob_key "$attr/$bundle"
  cat "$file"
}

# ------------------------------------------------------------------- receiving

# Everything about a bundle that has to be true before tar is invoked at all.
saves_verify_bundle() {
  local attr="$1" bundle="$2" expect_hash="$3" manifest
  manifest="$(saves_manifest "$attr")"

  local got
  got="$(saves_hash "$bundle")"
  [[ "$got" == "$expect_hash" ]] ||
    die "the bundle downloaded for $attr is not the one the remote said it was.
     expected $expect_hash
     got      $got
     Nothing local has been touched."

  # Every member must be a regular file. Bundles are built from regular files
  # only, so anything else — a symlink, a hardlink, a device, a fifo — is either
  # corruption or an attempt at writing somewhere it should not. A symlink
  # followed by a write to it is the classic way out of an extraction directory.
  local kinds
  kinds="$(tar -tvf "$bundle" | cut -c1 | LC_ALL=C sort -u | tr -d '\n')"
  [[ "$kinds" == "-" ]] ||
    die "the bundle for $attr holds something that is not a plain file (types: $kinds).
     Refusing to extract it. Nothing local has been touched."

  local globs=()
  mapfile -t globs < <(jq -r '.saves[]?' <<<"$manifest")

  local name
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    [[ "$name" != /* ]] ||
      die "the bundle for $attr holds an absolute path ($name). Refusing to extract it."
    case "/$name/" in
      */../*) die "the bundle for $attr holds a path that climbs out of it ($name). Refusing to extract it." ;;
    esac
    saves_matches_globs "$name" "${globs[@]}" ||
      die "the bundle for $attr holds '$name', which is not something this environment declares as a save.
     Refusing to extract it."
  done < <(tar -tf "$bundle")
}

saves_matches_globs() {
  local name="$1"
  shift
  local glob
  for glob in "$@"; do
    # shellcheck disable=SC2053 # glob comparison is the point
    [[ "$name" == $glob ]] && return 0
  done
  return 1
}

# Extract to an empty directory and move the files in one at a time.
#
# Not a wholesale directory move: the state directory holds things that are not
# saves — a Harkinian .o2r among them — and replacing a subtree would take them
# with it. Nothing local is removed for being absent from the bundle either;
# for saves, the safe direction is always "keep both".
saves_extract() {
  local attr="$1" bundle="$2" state staging
  state="$(env_state_dir "$attr")"
  staging="$(saves_tmp)/extract"
  rm -rf "$staging"
  mkdir -p "$staging"

  tar --no-same-owner --no-same-permissions --no-overwrite-dir \
    --delay-directory-restore -C "$staging" -xf "$bundle"

  mkdir -p "$state"
  local file
  while IFS= read -r -d '' file; do
    file="${file#./}"
    mkdir -p "$state/$(dirname "$file")"
    mv -f "$staging/$file" "$state/$file"
  done < <(cd "$staging" && find . -type f -print0)
  rm -rf "$staging"
}

# Archive whatever is here before overwriting it. A pull is the one operation
# that can destroy local work, so it never happens without this first.
saves_snapshot() {
  local attr="$1" tmp hash dir file
  tmp="$(saves_tmp)"
  saves_bundle "$attr" "$tmp/snapshot.tar.zst" || return 0

  hash="$(saves_hash "$tmp/snapshot.tar.zst")"
  dir="$GOTG_SAVES_DIR/local/$attr"
  mkdir -p "$dir"
  file="$dir/$(iso_now | tr -d ':')-${hash:0:12}.tar.zst"
  mv "$tmp/snapshot.tar.zst" "$file"
  log "archived the saves that were here: $file"
}
