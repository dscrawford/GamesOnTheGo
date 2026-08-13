# shellcheck shell=bash
# `gotg saves` — moving saves between machines.
#
# A separate noun because `gotg sync` already means "rebuild the nix GC roots",
# and one command that sometimes rebuilds emulators and sometimes uploads saves
# would be a bad thing to mistype.
#
# What moves is a generation: one bundle per environment, addressed by its
# content, holding paths relative to the state directory and nothing about the
# machine that made it. The GOTG service keeps the newest few generations and
# decides conflicts; which files a bundle becomes, and where, is decided by
# the machine pulling it at the moment it pulls it.

saves_usage() {
  cat <<'EOF'
usage: gotg saves <command> [args]

  setup [url]            point at the GOTG service, and check it answers
  status [<id>|--all]    compare this machine with the service; writes nothing
  push   [<id>|--all]    send this machine's saves         [--force]
  pull   [<id>|--all]    take the service's saves
  adopt  [<id>|--all]    copy in saves from before the emulators were
                         told where to put them          [--yes]

An id is resolved the same way `play` resolves it, but saves belong to the
*environment*, which several games can share — env-snes holds every SNES memory
save. The commands say which environment they are working on.

A push that would overwrite work done elsewhere stops and says so. A pull
archives what was here first, and the last few generations are kept on both
sides.
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

# Point at the GOTG service. The same api.json carries the artwork requests,
# so a machine set up for one is set up for both — which is the point of there
# being one service. Merged rather than written, for the same reason
# config_patch merges: setting up saves must not unconfigure anything else.
saves_cmd_setup() {
  local url="${1:-}"

  if [[ -n "$url" ]]; then
    [[ "$url" == http://* || "$url" == https://* ]] ||
      die "the service is an http(s) URL: $url"
    local token existing='{}' file
    file="$(saves_api_file)"
    token="$(prompt_secret "token for ${url%/}: ")"
    [[ -n "$token" ]] || die "a token is required — it is what keeps the service closed"

    mkdir -p "$GOTG_CONFIG_DIR"
    chmod 700 "$GOTG_CONFIG_DIR"
    [[ -f "$file" ]] && existing="$(cat "$file")"
    : >"$file.tmp"
    chmod 600 "$file.tmp"
    jq -n --argjson existing "$existing" --arg url "${url%/}" --arg token "$token" \
      '$existing + {url: $url, token: $token}' >"$file.tmp"
    mv "$file.tmp" "$file"
  fi

  saves_have_remote ||
    die "no service configured. Point at one with:
       gotg saves setup https://gotg-api.example.net"

  # Prove it answers with this token rather than claiming it does. A 404 for
  # an environment nobody has pushed is the service working; store_meta dies
  # by itself on a refused token.
  log "checking the service…"
  local rc=0
  store_meta env-setup-probe >/dev/null || rc=$?
  ((rc == 2)) && die "could not reach the GOTG service at $(saves_api_url)"
  ((rc == 3)) && die "the GOTG service refused the token — it may be revoked or expired; run: gotg login"
  log "  reachable, and the token is accepted"
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

  printf '%s%s%s\n' "$C_HEAD" "$attr" "$C_RESET"
  if [[ -z "$hash" ]]; then
    printf '  %slocal  %s no saves here yet\n' "$C_MUTED" "$C_RESET"
  else
    # Unpushed work is the one thing on this line that might need acting on,
    # so it is the one thing that is not the default colour.
    local changed=""
    if [[ "$hash" != "$pushed" && "$hash" != "$base_hash" ]]; then
      changed="$C_WARN, changed since the last sync$C_RESET"
    fi
    printf '  %slocal  %s %s file(s), %s, generation %s%s\n' \
      "$C_MUTED" "$C_RESET" \
      "$files" "$(human_size "$(stat -c '%s' "$tmp/status.tar.zst")")" "$base_gen" \
      "$changed"
  fi

  if ! saves_have_remote; then
    printf '  %sremote %s none configured — gotg saves setup <url>\n' "$C_MUTED" "$C_RESET"
    return 0
  fi

  # An unreachable service is a fact to report, not a failure: this command is
  # most useful precisely when something is wrong. The service tells "nothing
  # pushed" and "cannot ask" apart, so the report can too.
  local latest rc=0
  latest="$(store_meta "$attr" 2>/dev/null)" || rc=$?
  if ((rc == 1)); then
    printf '  %sremote %s nothing pushed yet\n' "$C_MUTED" "$C_RESET"
    return 0
  elif ((rc == 3)); then
    printf '  %sremote %s token refused — run: gotg login\n' "$C_MUTED" "$C_RESET"
    return 0
  elif ((rc != 0)); then
    printf '  %sremote %s unreachable\n' "$C_MUTED" "$C_RESET"
    return 0
  fi

  local rgen rdev rat rsize
  rgen="$(jq -r '.generation' <<<"$latest")"
  rdev="$(jq -r '.device // "?"' <<<"$latest")"
  rat="$(jq -r '.written_at // "?"' <<<"$latest")"
  rsize="$(jq -r '.size // 0' <<<"$latest")"
  printf '  %sremote %s generation %s from device %s, %s, %s\n' \
    "$C_MUTED" "$C_RESET" "$rgen" "$rdev" "$(human_size "$rsize")" "$rat"

  if [[ -n "$hash" && "$hash" == "$(jq -r '.hash' <<<"$latest")" ]]; then
    printf '  in step with the remote\n'
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

  saves_have_remote ||
    die "no remote configured. Point at one with: gotg saves setup <url>"

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

  log "$attr: pushing $(saves_member_count "$attr") file(s), $(human_size "$(stat -c '%s' "$bundle")")"

  # One request: the parent hash rides along, and the service either advances
  # the head atomically or answers with what is actually there. There is no
  # read-compare-write window for another machine to slip through.
  local meta rc=0
  meta="$(store_save "$attr" "$bundle" "$(saves_journal_get "$attr" base_hash)" "$force")" || rc=$?
  if ((rc == 2)); then
    saves_divergence "$attr" "$meta" "$bundle"
  elif ((rc != 0)); then
    # store_save already said what went wrong, from inside the substitution.
    exit 1
  fi

  local gen
  gen="$(jq -r '.generation' <<<"$meta")"
  saves_journal_set "$attr" "$(jq -nc \
    --argjson generation "$gen" --arg hash "$hash" \
    '{base_generation: $generation, base_hash: $hash, pushed_hash: $hash}')"
  log "$attr: pushed generation $gen"
}

# The 409 body is the head this push lost to, which is everything a person
# needs to choose a side.
saves_divergence() {
  local attr="$1" head="$2" bundle="$3"
  local rgen rdev rat rsize base_gen
  rgen="$(jq -r '.generation' <<<"$head")"
  rdev="$(jq -r '.device // "?"' <<<"$head")"
  rat="$(jq -r '.written_at // "?"' <<<"$head")"
  rsize="$(jq -r '.size // 0' <<<"$head")"
  base_gen="$(saves_journal_get "$attr" base_generation)"
  : "${base_gen:=0}"

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

  saves_have_remote ||
    die "no remote configured. Point at one with: gotg saves setup <url>"

  local attr
  for attr in "${attrs[@]}"; do
    saves_pull_one "$attr"
  done
}

saves_pull_one() {
  local attr="$1" tmp latest rc=0
  tmp="$(saves_tmp)"

  latest="$(store_meta "$attr")" || rc=$?
  if ((rc == 1)); then
    log "$attr: nothing has been pushed yet"
    return 0
  elif ((rc == 3)); then
    die "the GOTG service refused the token — it may be revoked or expired; run: gotg login"
  elif ((rc != 0)); then
    die "$attr: could not reach the GOTG service at $(saves_api_url)"
  fi

  local rhash rgen rsize
  rhash="$(jq -r '.hash' <<<"$latest")"
  rgen="$(jq -r '.generation' <<<"$latest")"
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

  # A pull is the one operation that can overwrite local work, so what is here
  # is archived first.
  saves_snapshot "$attr"

  # The retrieve says which generation its bytes are — the head can have moved
  # since the meta was read, and the journal must record what actually arrived.
  # store_retrieve hands curl the size cap, so an oversized answer is refused
  # in flight rather than measured afterwards.
  log "$attr: fetching generation $rgen ($(human_size "$rsize"))"
  local got fetch_rc=0
  got="$(store_retrieve "$attr" "$tmp/pull.tar.zst")" || fetch_rc=$?
  ((fetch_rc == 3)) &&
    die "the GOTG service refused the token — it may be revoked or expired; run: gotg login"
  ((fetch_rc != 0)) &&
    die "$attr: could not download the bundle from $(saves_api_url)"
  read -r rgen rhash <<<"$got"

  saves_verify_bundle "$attr" "$tmp/pull.tar.zst" "$rhash"
  saves_extract "$attr" "$tmp/pull.tar.zst"

  saves_journal_set "$attr" "$(jq -nc --argjson g "$rgen" --arg h "$rhash" --arg at "$(iso_now)" \
    '{base_generation: $g, base_hash: $h, pushed_hash: $h, pulled_at: $at}')"
  log "$attr: now at generation $rgen"
}

# Take the remote's save on the way into a game, but only when doing so cannot
# lose anything: the remote must have moved on, and the local tree must be
# unchanged since the last sync. Divergence is a decision for a person, made
# with `gotg saves status` — a launch is the wrong place to make it, so it is
# named and left alone. Never fatal: the game starting matters more.
saves_pull_auto() {
  local attr="$1"
  saves_have_remote || return 0
  [[ -f "$(env_saves_manifest "$attr")" ]] || return 0

  local latest rgen base_gen
  latest="$(store_meta "$attr" 2>/dev/null)" || return 0
  rgen="$(jq -r '.generation' <<<"$latest")"
  base_gen="$(saves_journal_get "$attr" base_generation)"
  : "${base_gen:=0}"
  ((rgen > base_gen)) || return 0

  local tmp current="" base_hash pushed
  tmp="$(saves_tmp)"
  if saves_bundle "$attr" "$tmp/auto.tar.zst" 2>/dev/null; then
    current="$(saves_hash "$tmp/auto.tar.zst")"
  fi
  base_hash="$(saves_journal_get "$attr" base_hash)"
  pushed="$(saves_journal_get "$attr" pushed_hash)"
  if [[ -n "$current" && "$current" != "$base_hash" && "$current" != "$pushed" ]]; then
    warn "$attr: this machine and the remote both have new saves. Launching with
     what is here; reconcile with: gotg saves status"
    return 0
  fi

  saves_pull_one "$attr"
}
