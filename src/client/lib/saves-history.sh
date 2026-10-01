# shellcheck shell=bash
# `gotg saves list` and `gotg saves restore` -- going back to an earlier save.
#
# What the picker's Saves list and the overlay's are made of. A save is a
# whole environment's bundle at one point: one of the generations the service
# keeps, or one of this machine's own archives -- the ones every pull and
# every restore make of what was here before writing over it. Both kinds are
# listed together, newest first, because to the person choosing they are the
# same thing: a point their game was at.
#
# Restoring writes the chosen point over the environment's saves, after
# archiving what is there, and then tells the journal the service's head is
# where this machine stands. That is what makes the restored save the next
# generation on the next push, rather than a conflict with the head it is
# older than -- and what keeps a launch from pulling the head straight back
# over it.

# A local archive's name: when it was made, colons dropped, and the start of
# its hash. Anything else under local/ is not ours to offer.
SAVES_LOCAL_RE='^([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z-[0-9a-f]{12}\.tar\.zst$'

# An environment one game has to itself, as against a platform's, which holds
# every game on it without its own file: env-n64-usa_donkey_kong_64, not
# env-n64. Platform names have no dash, so the second one is the game.
saves_dedicated() { [[ "$1" =~ ^env-[a-z0-9]+-[a-z0-9] ]]; }

saves_cmd_list() {
  config_load
  local want="" variant="" json="no" arg
  for arg in "$@"; do
    case "$arg" in
      --json) json="yes" ;;
      *) if [[ -z "$want" ]]; then want="$arg"; else variant="$arg"; fi ;;
    esac
  done
  [[ -n "$want" ]] || die "which game? usage: gotg saves list <id> [variant] [--json]"

  local attrs=() attr
  mapfile -t attrs < <(saves_resolve "$want" "$variant")
  for attr in "${attrs[@]}"; do
    if [[ "$json" == "yes" ]]; then
      saves_list_one "$attr"
    else
      saves_list_say "$(saves_list_one "$attr")"
    fi
  done
}

# One JSON object: the environment, whether a game has it to itself, whether
# the service could be asked, and every save, newest first. Each save has an
# `id` that `restore` takes back, and `here` when it is what is on disk now.
saves_list_one() {
  local attr="$1" tmp here=""
  tmp="$(saves_tmp)"
  if saves_bundle "$attr" "$tmp/list.tar.zst" 2>/dev/null; then
    here="$(saves_hash "$tmp/list.tar.zst")"
  fi

  local remote='[]' offline="true" history rc=0
  if saves_have_remote; then
    history="$(store_history "$attr" 2>/dev/null)" || rc=$?
    if ((rc == 0)); then
      offline="false"
      remote="$(jq -c '[.generations[] | {
        id: "remote:\(.generation)", source: "remote", generation, hash,
        device: (.device // ""), written_at, size}]' <<<"$history")"
    fi
  fi

  jq -nc --arg attr "$attr" --arg here "$here" --argjson remote "$remote" \
    --argjson local "$(saves_local_archives "$attr")" \
    --argjson offline "$offline" --argjson dedicated "$(saves_dedicated "$attr" && echo true || echo false)" '
    ($remote | map(.hash)) as $kept
    | ($local | map(select(.hash as $h | $kept | index($h) | not))
              | unique_by(.hash)) as $own
    | {attr: $attr, dedicated: $dedicated, offline: $offline,
       saves: ($remote + $own
               | map(. + {here: (.hash == $here)})
               | sort_by(.written_at, (.generation // 0)) | reverse)}'
}

# This machine's archives of one environment, as list entries.
saves_local_archives() {
  local attr="$1" dir file name
  dir="$GOTG_SAVES_DIR/local/$attr"
  {
    if [[ -d "$dir" ]]; then
      for file in "$dir"/*.tar.zst; do
        [[ -f "$file" ]] || continue
        name="$(basename "$file")"
        [[ "$name" =~ $SAVES_LOCAL_RE ]] || continue
        jq -nc --arg name "$name" --arg hash "$(saves_hash "$file")" \
          --arg device "$(device_name)" --argjson size "$(stat -c '%s' "$file")" \
          --arg at "${BASH_REMATCH[1]}T${BASH_REMATCH[2]}:${BASH_REMATCH[3]}:${BASH_REMATCH[4]}Z" \
          '{id: "local:\($name)", source: "local", generation: null, hash: $hash,
            device: $device, written_at: $at, size: $size}'
      done
    fi
  } | jq -sc '.'
}

saves_list_say() {
  local list="$1"
  printf '%s%s%s%s\n' "$C_HEAD" "$(jq -r .attr <<<"$list")" "$C_RESET" \
    "$(jq -r 'if .dedicated then "" else "  (shared by every game on its platform)" end' <<<"$list")"
  jq -r 'if .offline then "  the service could not be asked; this machine'"'"'s archives only" else empty end' <<<"$list"
  jq -r 'if (.saves | length) == 0 then "  no saves yet" else empty end' <<<"$list"
  jq -r '.saves[] | "  \(.id)\t\(.written_at)  on \(if .device == "" then "?" else .device end)" +
    (if .here then "  (what is here now)" else "" end)' <<<"$list"
}

# Put one save back: `remote:<generation>` or `local:<archive>`, as `list`
# names them.
saves_cmd_restore() {
  config_load
  local want="" variant="" which="" arg
  for arg in "$@"; do
    case "$arg" in
      remote:* | local:*) which="$arg" ;;
      *) if [[ -z "$want" ]]; then want="$arg"; else variant="$arg"; fi ;;
    esac
  done
  [[ -n "$want" && -n "$which" ]] ||
    die "usage: gotg saves restore <id> [variant] remote:<generation>|local:<archive>
     \`gotg saves list <id>\` names them."

  local attrs=()
  mapfile -t attrs < <(saves_resolve "$want" "$variant")
  [[ ${#attrs[@]} -eq 1 ]] || die "restore puts back one environment's saves at a time"
  saves_restore_one "${attrs[0]}" "$which"
}

saves_restore_one() {
  local attr="$1" which="$2" tmp bundle hash
  tmp="$(saves_tmp)"
  bundle="$tmp/restore.tar.zst"

  # Fetched and verified before anything here is touched: a save that cannot
  # be had, or is not what it says, leaves the game as it was.
  case "$which" in
    remote:*)
      local generation="${which#remote:}" got rc=0
      [[ "$generation" =~ ^[0-9]+$ ]] || die "not a generation number: $generation"
      saves_have_remote || die "no service configured to fetch $which from"
      got="$(store_retrieve "$attr" "$bundle" "$generation")" || rc=$?
      ((rc == 1)) && die "generation $generation of $attr is not kept by the service any more"
      ((rc == 3)) && die "the GOTG service refused the token — it may be revoked or expired; run: gotg login"
      ((rc != 0)) && die "$attr: could not reach the GOTG service at $(saves_api_url)"
      read -r _ hash <<<"$got"
      ;;
    local:*)
      local name="${which#local:}"
      [[ "$name" =~ $SAVES_LOCAL_RE ]] || die "not one of this machine's archives: $name"
      [[ -f "$GOTG_SAVES_DIR/local/$attr/$name" ]] ||
        die "no archive $name for $attr here. \`gotg saves list\` names the ones there are."
      # Copied out first: archiving what is here below can prune this very one.
      cp "$GOTG_SAVES_DIR/local/$attr/$name" "$bundle"
      hash="$(saves_hash "$bundle")"
      ;;
    *) die "not a save: $which (remote:<generation> or local:<archive>)" ;;
  esac
  saves_verify_bundle "$attr" "$bundle" "$hash"

  local before=() state
  state="$(env_state_dir "$attr")"
  if saves_bundle "$attr" "$tmp/before.tar.zst" 2>/dev/null; then
    if [[ "$(saves_hash "$tmp/before.tar.zst")" == "$hash" ]]; then
      log "$attr: $which is what is here already"
      saves_restore_journal "$attr"
      return 0
    fi
    mapfile -t before < <(tar -tf "$tmp/before.tar.zst")
  fi
  saves_snapshot "$attr"

  # A point in the game is the files it had, so the save files made since go:
  # a memory card or slot that did not exist then would otherwise still be
  # there. Only files the environment declares as saves -- they are what the
  # archive above holds.
  local file
  while IFS= read -r file; do
    [[ -n "$file" ]] && rm -f -- "$state/$file"
  done < <(comm -23 <(printf '%s\n' "${before[@]}" | LC_ALL=C sort) \
    <(tar -tf "$bundle" | LC_ALL=C sort))

  saves_extract "$attr" "$bundle"
  saves_restore_journal "$attr"
  log "$attr: restored $which"
}

# The service's head is where this machine now stands, and what is here is a
# change on top of it: the next push makes the restored save the newest
# generation, and a launch does not pull the head back over it. Offline the
# journal is left alone, which says the same thing to the next sync.
saves_restore_journal() {
  local attr="$1" head
  saves_have_remote || return 0
  head="$(store_meta "$attr" 2>/dev/null)" || return 0
  saves_journal_set "$attr" "$(jq -c '{base_generation: .generation, base_hash: .hash,
    pushed_hash: "", base_written_at: (.written_at // "")}' <<<"$head")"
}
