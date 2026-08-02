# shellcheck shell=bash
# `gotg saves` — moving saves between machines.
#
# A separate noun because `gotg sync` already means "rebuild the nix GC roots",
# and one command that sometimes rebuilds emulators and sometimes uploads saves
# would be a bad thing to mistype.

saves_usage() {
  cat <<'EOF'
usage: gotg saves <command> [args]

  setup [backend]        choose where saves live, and check it works
  status [<id>|--all]    compare this machine with the remote; writes nothing
  push   [<id>|--all]    send this machine's saves        [--force]
  pull   [<id>|--all]    take the remote's saves          [--force]
  adopt  [<id>|--all]    copy in saves from before the emulators were
                         told where to put them          [--yes]

An id is resolved the same way `play` resolves it, but saves belong to the
*environment*, which several games can share — env-snes holds every SNES memory
save. The commands say which environment they are working on.

Nothing here ever deletes a save. A push that would overwrite work done
elsewhere stops and says so; a pull archives what was here first.
EOF
}

cmd_saves() {
  local verb="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  # In the command's own shell, so the cleanup trap belongs to the process that
  # is going to exit rather than to a subshell that is about to.
  saves_tmp_init
  case "$verb" in
    setup) saves_cmd_setup "$@" ;;
    status) saves_cmd_status "$@" ;;
    push) saves_cmd_push "$@" ;;
    pull) saves_cmd_pull "$@" ;;
    adopt) saves_cmd_adopt "$@" ;;
    help | --help | -h | "") saves_usage ;;
    *)
      printf 'error: unknown saves command: %s\n\n' "$verb" >&2
      saves_usage >&2
      exit 1
      ;;
  esac
}

# A game id, or --all for every environment built here. A bare id is resolved
# through the catalog exactly as `play` does, so the two can never disagree
# about which environment a game belongs to.
saves_resolve() {
  local want="${1:-}"
  if [[ -z "$want" || "$want" == "--all" ]]; then
    local root name
    [[ -d "$GOTG_ROOTS_DIR" ]] || return 0
    for root in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$root" ]] || continue
      name="$(basename "$root")"
      [[ "$name" == env-* ]] || continue
      [[ -f "$(env_saves_manifest "$name")" ]] || continue
      printf '%s\n' "$name"
    done
    return 0
  fi

  manifest_cached || manifest_ensure
  local game attr
  game="$(manifest_find "$want")"
  attr="$(env_attr "$game")"
  # Say it out loud: several games can share one environment, so "pushing
  # usa.zelda" would be a small lie about what is being moved.
  log "$want runs in $attr, which is what saves belong to"
  printf '%s\n' "$attr"
}

saves_cmd_setup() {
  local backend="${1:-filebrowser}"
  case "$backend" in
    filebrowser) ;;
    rclone) die "the rclone backend is not built yet — use: gotg saves setup filebrowser" ;;
    *) die "unknown backend '$backend' (filebrowser)" ;;
  esac

  config_load
  config_set saves_backend "$backend"
  export GOTG_SAVES_BACKEND="$backend"
  log "saves backend: $backend"
  log "saves live under $(saves_root) on $GOTG_SERVER"

  # Prove it end to end rather than claiming it works. This also answers the
  # question the File Browser account raises: uploading needs Create and Modify,
  # and tidying old generations later will need Delete.
  local tmp probe="env-probe/latest.json"
  tmp="$(saves_tmp)"
  printf '{"version":1,"probe":true}' >"$tmp/probe.json"

  log "checking write access…"
  blob_put "$tmp/probe.json" "$probe"
  blob_get "$probe" "$tmp/probe-back.json"
  cmp -s "$tmp/probe.json" "$tmp/probe-back.json" ||
    die "wrote a probe file but read back something different — the backend is not usable"
  log "  upload and download: ok"

  if blob_delete "$probe"; then
    log "  delete: ok"
  else
    warn "could not delete the probe file at $(saves_root)/$probe.
     Saves will still work; tidying old generations will not, and that probe is
     left behind for you to remove."
  fi
  log ""
  log "Ready. Try: gotg saves status --all"
}

# What each side has. Writes nothing, and works with no network — a machine
# that cannot reach the server should still be able to say what it is holding.
saves_cmd_status() {
  # Reading the config is a local file read, so this stays usable with no
  # network — which is most of the point of a status command.
  config_load
  local attrs=()
  mapfile -t attrs < <(saves_resolve "${1:-}")
  [[ ${#attrs[@]} -gt 0 ]] || {
    log "nothing is built here yet"
    return 0
  }

  local attr
  for attr in "${attrs[@]}"; do
    saves_status_one "$attr"
  done
}

saves_status_one() {
  local attr="$1" tmp hash="" files=0
  tmp="$(saves_tmp)"

  if saves_bundle "$attr" "$tmp/status.tar.zst" 2>/dev/null; then
    hash="$(saves_hash "$tmp/status.tar.zst")"
    files="$(saves_member_count "$attr")"
  fi

  local base_gen base_hash pushed
  base_gen="$(saves_journal_get "$attr" base_generation)"
  base_hash="$(saves_journal_get "$attr" base_hash)"
  pushed="$(saves_journal_get "$attr" pushed_hash)"
  : "${base_gen:=0}"

  printf '%s\n' "$attr"
  if [[ -z "$hash" ]]; then
    printf '  local   no saves here yet\n'
  else
    printf '  local   %s file(s), %s, generation %s%s\n' \
      "$files" "$(human_size "$(stat -c '%s' "$tmp/status.tar.zst")")" "$base_gen" \
      "$(if [[ "$hash" != "$pushed" && "$hash" != "$base_hash" ]]; then printf ', changed since the last sync'; fi)"
  fi

  # A die inside the substitution ends only the subshell, which is what keeps
  # this usable on a train.
  local latest
  latest="$(saves_latest "$attr" 2>/dev/null)" || latest=""
  if [[ -z "$latest" ]]; then
    printf '  remote  nothing pushed yet, or the server is unreachable\n'
    return 0
  fi

  local rgen rdev rat rsize
  rgen="$(jq -r '.generation' <<<"$latest")"
  rdev="$(jq -r '.device // "?"' <<<"$latest")"
  rat="$(jq -r '.written_at // "?"' <<<"$latest")"
  rsize="$(jq -r '.size // 0' <<<"$latest")"
  printf '  remote  generation %s from device %s, %s, %s\n' \
    "$rgen" "$rdev" "$(human_size "$rsize")" "$rat"

  if [[ -n "$hash" && "$hash" == "$(jq -r '.hash' <<<"$latest")" ]]; then
    printf '  in step\n'
  elif ((rgen > base_gen)); then
    printf '  the remote has moved on since this machine last synced\n'
  fi
}

saves_cmd_push() {
  config_load
  local want="" force="no" arg
  for arg in "$@"; do
    case "$arg" in
      --force) force="force" ;;
      *) want="$arg" ;;
    esac
  done

  local attrs=()
  mapfile -t attrs < <(saves_resolve "$want")
  [[ ${#attrs[@]} -gt 0 ]] || die "nothing to push — no environment is built here"

  local attr
  for attr in "${attrs[@]}"; do
    saves_push_one "$attr" "$force"
  done
}

saves_push_one() {
  local attr="$1" force="$2" tmp bundle hash
  tmp="$(saves_tmp)"
  bundle="$tmp/push-$attr.tar.zst"

  if ! saves_bundle "$attr" "$bundle"; then
    log "$attr: no saves here to push"
    return 0
  fi
  hash="$(saves_hash "$bundle")"

  # An unchanged save set bundles to identical bytes, so a machine that has
  # already pushed does nothing at all — which is what makes it cheap enough to
  # run after every session.
  if [[ "$hash" == "$(saves_journal_get "$attr" pushed_hash)" ]]; then
    log "$attr: already up to date"
    return 0
  fi

  local latest="" rgen=0
  latest="$(saves_latest "$attr")" || latest=""
  [[ -n "$latest" ]] && rgen="$(jq -r '.generation' <<<"$latest")"

  local base_gen
  base_gen="$(saves_journal_get "$attr" base_generation)"
  : "${base_gen:=0}"

  if ((rgen > base_gen)) && [[ "$force" != "force" ]]; then
    saves_divergence "$attr" "$latest" "$bundle" "$base_gen"
  fi

  local gen key
  gen=$((rgen + 1))
  key="$(printf '%s/gen/%06d-%s.tar.zst' "$attr" "$gen" "${hash:0:12}")"

  log "$attr: pushing $(saves_member_count "$attr") file(s), $(human_size "$(stat -c '%s' "$bundle")"), as generation $gen"
  blob_put "$bundle" "$key"

  # The bundle is durable before the pointer moves, so the worst a race between
  # two machines can cost is a pointer — never a save.
  jq -n \
    --arg attr "$attr" \
    --argjson generation "$gen" \
    --arg parent "$(saves_journal_get "$attr" base_hash)" \
    --arg hash "$hash" \
    --arg bundle "${key#"$attr"/}" \
    --argjson size "$(stat -c '%s' "$bundle")" \
    --argjson files "$(saves_member_count "$attr")" \
    --arg device "$(device_id)" \
    --arg written_at "$(iso_now)" \
    '{version: 1, attr: $attr, generation: $generation, parent: $parent,
      hash: $hash, bundle: $bundle, size: $size, files: $files,
      device: $device, written_at: $written_at}' >"$tmp/publish.json"
  blob_put "$tmp/publish.json" "$attr/latest.json"

  saves_journal_set "$attr" "$(jq -nc \
    --argjson generation "$gen" --arg hash "$hash" \
    '{base_generation: $generation, base_hash: $hash, pushed_hash: $hash}')"

  # Neither backend offers compare-and-swap, so confirm the pointer is ours.
  local after
  after="$(saves_latest "$attr" 2>/dev/null)" || after=""
  if [[ -n "$after" && "$(jq -r '.hash' <<<"$after")" != "$hash" ]]; then
    warn "another machine published $attr at the same moment, and its pointer won.
     Your bundle is safe at $key — nothing was lost. To make it current:
       gotg saves push --force <id>"
  fi
  log "$attr: pushed generation $gen"
}

saves_divergence() {
  local attr="$1" latest="$2" bundle="$3" base_gen="$4"
  local rgen rdev rat rsize
  rgen="$(jq -r '.generation' <<<"$latest")"
  rdev="$(jq -r '.device // "?"' <<<"$latest")"
  rat="$(jq -r '.written_at // "?"' <<<"$latest")"
  rsize="$(jq -r '.size // 0' <<<"$latest")"

  die "$attr has moved on since this machine last synced.
     remote  generation $rgen  from device $rdev  $(human_size "$rsize")  $rat
     local   generation $base_gen  this device ($(device_id))  $(human_size "$(stat -c '%s' "$bundle")")  changed since the pull
   Nothing was uploaded. Both sides are intact; choose one:
     Take the remote (what is here is archived first):  gotg saves pull <id>
     Take yours (the remote generation is kept):        gotg saves push --force <id>
     Look before choosing:                              gotg saves status <id>"
}

saves_cmd_adopt() {
  local want="" apply="no" arg
  for arg in "$@"; do
    case "$arg" in
      --yes | -y) apply="yes" ;;
      *) want="$arg" ;;
    esac
  done

  local attrs=()
  mapfile -t attrs < <(saves_resolve "$want")
  [[ ${#attrs[@]} -gt 0 ]] || die "nothing to adopt into — no environment is built here"

  local attr total=0 found declared
  for attr in "${attrs[@]}"; do
    log "$attr"
    declared="$(jq -r '.legacy | length' <<<"$(saves_manifest "$attr")")"
    if [[ "$declared" == "0" ]]; then
      # Distinct from "nothing to copy": this environment has not been told
      # where its emulator used to write, so it is not looking.
      log "  no older location declared for this environment — nothing to look at"
      continue
    fi
    found="$(saves_adopt "$attr" "$apply")"
    [[ "$found" == "0" ]] && log "  nothing left behind to copy"
    total=$((total + found))
  done

  log ""
  if [[ "$apply" == "yes" ]]; then
    log "copied $total file(s). The originals are untouched — delete them yourself once"
    log "you have started each game and seen its save."
  else
    log "$total file(s) would be copied. Nothing has been changed."
    log "Run it again with --yes to do it."
  fi
}

saves_cmd_pull() {
  config_load
  local want="" arg
  for arg in "$@"; do
    case "$arg" in
      --force) ;;
      *) want="$arg" ;;
    esac
  done

  local attrs=()
  mapfile -t attrs < <(saves_resolve "$want")
  [[ ${#attrs[@]} -gt 0 ]] || die "nothing to pull into — no environment is built here"

  local attr
  for attr in "${attrs[@]}"; do
    saves_pull_one "$attr"
  done
}

saves_pull_one() {
  local attr="$1" tmp latest
  tmp="$(saves_tmp)"

  latest="$(saves_latest "$attr")" || {
    log "$attr: nothing has been pushed yet"
    return 0
  }

  local rhash rgen rbundle rsize
  rhash="$(jq -r '.hash' <<<"$latest")"
  rgen="$(jq -r '.generation' <<<"$latest")"
  rbundle="$(jq -r '.bundle' <<<"$latest")"
  rsize="$(jq -r '.size // 0' <<<"$latest")"

  local current=""
  if saves_bundle "$attr" "$tmp/current.tar.zst" 2>/dev/null; then
    current="$(saves_hash "$tmp/current.tar.zst")"
  fi
  if [[ "$current" == "$rhash" ]]; then
    log "$attr: already has generation $rgen"
    saves_journal_set "$attr" "$(jq -nc --argjson g "$rgen" --arg h "$rhash" \
      '{base_generation: $g, base_hash: $h}')"
    return 0
  fi

  # Refuse on the declared size before downloading a byte of it.
  local max
  max="$(saves_max_bytes)"
  ((rsize <= max)) ||
    die "$attr: the remote bundle claims to be $(human_size "$rsize"), over the $(human_size "$max") limit. Not downloading it."

  saves_snapshot "$attr"

  log "$attr: fetching generation $rgen ($(human_size "$rsize"))"
  blob_get "$attr/$rbundle" "$tmp/pull.tar.zst" ||
    die "$attr: could not download $rbundle"

  # The name carries the first twelve of the hash, so a bundle is identifiable
  # from a listing alone; check it against the name as well as against the
  # pointer, since the two could disagree only if something is wrong.
  local named="${rbundle##*-}"
  named="${named%.tar.zst}"
  [[ "${rhash:0:12}" == "$named" ]] ||
    die "$attr: $rbundle is named for a different bundle than latest.json points at. Refusing to extract it."

  saves_verify_bundle "$attr" "$tmp/pull.tar.zst" "$rhash"
  saves_extract "$attr" "$tmp/pull.tar.zst"

  saves_journal_set "$attr" "$(jq -nc --argjson g "$rgen" --arg h "$rhash" --arg at "$(iso_now)" \
    '{base_generation: $g, base_hash: $h, pushed_hash: $h, pulled_at: $at}')"
  log "$attr: now at generation $rgen"
}
