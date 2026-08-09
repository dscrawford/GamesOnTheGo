# shellcheck shell=bash
# `gotg saves` — moving saves between machines.
#
# A separate noun because `gotg sync` already means "rebuild the nix GC roots",
# and one command that sometimes rebuilds emulators and sometimes uploads saves
# would be a bad thing to mistype.
#
# The moving itself is Ludusavi's; see lib/ludusavi.sh for what gotg keeps hold
# of and why. What is here is the command surface: which environment, which
# direction, and what to refuse.

saves_usage() {
  cat <<'EOF'
usage: gotg saves <command> [args]

  setup [url]            point at the remote, and check it works
  status [<id>|--all]    compare this machine with the remote; writes nothing
  push   [<id>|--all]    send this machine's saves
  pull   [<id>|--all]    take the remote's saves
  adopt  [<id>|--all]    copy in saves from before the emulators were
                         told where to put them          [--yes]

An id is resolved the same way `play` resolves it, but saves belong to the
*environment*, which several games can share — env-snes holds every SNES memory
save. The commands say which environment they are working on.

Nothing here ever deletes a save. A pull archives what was here first, and the
last few backups are kept on both sides.
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

  # An attribute names an environment directly. Anything else is a game, and is
  # looked up — which needs the catalog, and a network if it is not cached.
  if [[ "$want" == env-* ]]; then
    [[ -f "$(env_saves_manifest "$want")" ]] ||
      die "$want is not built here, so there is nothing that knows what its saves are.
     Build it with: gotg install <id>"
    printf '%s\n' "$want"
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

saves_targets() {
  local attrs=()
  mapfile -t attrs < <(saves_resolve "${1:-}")
  [[ ${#attrs[@]} -gt 0 ]] || return 1
  printf '%s\n' "${attrs[@]}"
}

saves_cmd_setup() {
  local url="${1:-}"
  config_load

  if [[ -n "$url" ]]; then
    mkdir -p "$GOTG_CONFIG_DIR"
    chmod 700 "$GOTG_CONFIG_DIR"
    local user pass
    read -r -p "username for $url: " user
    read -r -s -p "password: " pass
    printf '\n'
    jq -n --arg url "$url" --arg username "$user" --arg password "$pass" \
      '{type: "webdav", url: $url, username: $username, password: $password}' \
      >"$(lud_remote_file)"
    chmod 600 "$(lud_remote_file)"
  fi

  lud_have_remote ||
    die "no remote configured. Point at one with:
       gotg saves setup https://saves.example.net"

  local attrs=()
  mapfile -t attrs < <(saves_resolve --all)
  lud_write_config "${attrs[@]}"

  # Prove it end to end rather than claiming it works: a directory created and
  # removed is exactly the access a push and a tidy-up need.
  log "checking the remote…"
  local conf probe="gotg-setup-probe"
  conf="$(lud_rclone_conf)"
  rclone --config "$conf" mkdir "$LUD_REMOTE:$probe" ||
    die "could not write to the remote. Nothing has been changed."
  rclone --config "$conf" rmdir "$LUD_REMOTE:$probe" ||
    warn "wrote to the remote but could not remove the probe directory $probe.
     Saves will still work; tidying old ones will not."
  log "  upload and delete: ok"
  log ""
  log "Ready. Try: gotg saves status --all"
}

# What each side has. Writes nothing, and works with no network — a machine that
# cannot reach the server should still be able to say what it is holding.
saves_cmd_status() {
  config_load
  local attrs=()
  mapfile -t attrs < <(saves_resolve "${1:-}") || true
  [[ ${#attrs[@]} -gt 0 ]] || {
    log "nothing is built here yet"
    return 0
  }

  lud_write_config "${attrs[@]}"
  local attr
  for attr in "${attrs[@]}"; do
    saves_status_one "$attr"
  done
}

saves_status_one() {
  local attr="$1" scan
  printf '%s%s%s\n' "$C_HEAD" "$attr" "$C_RESET"

  if ! scan="$(lud_scan backup "$attr")"; then
    printf '  %slocal  %s no saves here yet\n' "$C_MUTED" "$C_RESET"
  else
    # Unpushed work is the one thing on this line that might need acting on, so
    # it is the one thing that is not the default colour.
    local changed="" n
    n="$(lud_changed_count "$scan")"
    ((n > 0)) && changed="$C_WARN, changed since the last sync$C_RESET"
    printf '  %slocal  %s %s file(s), %s%s\n' \
      "$C_MUTED" "$C_RESET" \
      "$(lud_file_count "$scan")" "$(human_size "$(lud_byte_count "$scan")")" "$changed"
  fi

  if ! lud_have_remote; then
    printf '  %sremote %s none configured — gotg saves setup <url>\n' "$C_MUTED" "$C_RESET"
    return 0
  fi

  local pending
  # Named, so the count belongs to this environment. Without the name the
  # preview is of every environment at once, and printing that same number
  # under each one in turn says something untrue about all but one of them.
  #
  # A remote that cannot be reached is a fact to report, not a failure: this
  # command is most useful precisely when something is wrong.
  if ! pending="$(lud_run cloud upload --preview "$attr" 2>&1)"; then
    printf '  %sremote %s unreachable\n' "$C_MUTED" "$C_RESET"
    return 0
  fi
  # Ludusavi prints one bracketed line per file it would move.
  local changes
  changes="$(grep -c '^\[' <<<"$pending" || true)"
  if ((changes == 0)); then
    printf '  in step with the remote\n'
  else
    printf '  %s change(s) to send\n' "$changes"
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
  lud_write_config "${attrs[@]}"

  local attr scan changing=()
  for attr in "${attrs[@]}"; do
    if ! scan="$(lud_scan backup "$attr")"; then
      log "$attr: no saves here to push"
      continue
    fi
    # Before the backup, so a save set that should never have been collected is
    # refused before it is copied anywhere at all.
    lud_check_size "$attr" "$scan"
    if [[ "$(lud_changed_count "$scan")" == "0" ]]; then
      log "$attr: already up to date"
      continue
    fi
    changing+=("$attr")
  done

  if [[ ${#changing[@]} -gt 0 ]]; then
    log "backing up ${changing[*]}"
    lud_run backup --force "${changing[@]}" >/dev/null ||
      die "ludusavi could not back up ${changing[*]}. Nothing was uploaded."
  fi

  lud_have_remote || {
    log "no remote configured, so nothing was uploaded — gotg saves setup <url>"
    return 0
  }

  # An upload mirrors this machine over the remote, so anything up there that
  # has not been taken down first would go. Asking what a download would bring
  # is the same question as "has someone else played since I last synced?", and
  # it is asked before the mirroring rather than discovered afterwards.
  if [[ "$force" != "force" ]]; then
    local incoming=""
    incoming="$(lud_run cloud download --preview "${attrs[@]}" 2>&1)" || incoming=""
    if grep -q '^\[' <<<"$incoming"; then
      die "the remote has saves this machine has not taken yet.
     Nothing was uploaded. Both sides are intact; choose one:
       Take the remote first (what is here is archived):  gotg saves pull ${attrs[0]}
       Keep yours and replace the remote:                 gotg saves push --force ${attrs[0]}
       Look before choosing:                              gotg saves status ${attrs[0]}"
    fi
  fi

  lud_run cloud upload --force "${attrs[@]}" >/dev/null ||
    die "could not upload to the remote. What is here is backed up and intact."
  log "pushed"
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
  lud_write_config "${attrs[@]}"

  lud_have_remote ||
    die "no remote configured. Point at one with: gotg saves setup <url>"

  lud_run cloud download --force "${attrs[@]}" >/dev/null ||
    die "could not download from the remote. Nothing local has been touched."

  local attr
  for attr in "${attrs[@]}"; do
    saves_pull_one "$attr"
  done
}

saves_pull_one() {
  local attr="$1" scan
  if ! scan="$(lud_scan restore "$attr")"; then
    log "$attr: nothing has been pushed yet"
    return 0
  fi

  # Where the backup wants to write, checked before anything is written.
  local recorded state
  recorded="$(lud_recorded_root "$attr" "$scan")"
  state="$(env_state_dir "$attr")"

  if [[ "$recorded" != "$state" ]]; then
    # The other machine keeps this environment somewhere else. Move the root,
    # whole — which is safe precisely because every path in it was just checked
    # to be under that root.
    log "$attr: these saves were written under $recorded; restoring them into $state"
    LUD_REDIRECT_FROM="$recorded" LUD_REDIRECT_TO="$state" lud_write_config "$attr"
  fi

  # A pull is the one operation that can overwrite local work, so what is here
  # is archived first — into gotg's own directory, not Ludusavi's, because the
  # next cloud download mirrors the remote over Ludusavi's and would take an
  # archive kept there with it.
  saves_snapshot "$attr"

  log "$attr: restoring $(lud_file_count "$scan") file(s)"
  lud_run restore --force "$attr" >/dev/null ||
    die "$attr: ludusavi could not restore. What was here is archived and intact."
  log "$attr: restored"

  # Leave the config as the rest of the commands expect to find it.
  lud_write_config "$attr"
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
