# shellcheck shell=bash
# Fetching a game from the server.
#
# Downloads land in a staging directory first and are only moved into place once
# complete and verified, so an interrupted transfer can never look like an
# installed game — which matters when the next thing to touch it is Steam.

# Poll interval for the graphical progress dialog.
PROGRESS_TICK="${GOTG_PROGRESS_TICK:-0.5}"

# Where an entry's raw members stage before install: one directory per game,
# member names (nested included) preserved inside it.
download_partial_path() {
  printf '%s/%s' "$GOTG_PARTIAL_DIR" "$1"
}

# curl with resume, always: every member is a stable file on the service. The
# If-Range validator makes a resume against changed bytes restart cleanly
# instead of splicing two files.
_curl_download() {
  local url="$1" out="$2" etag="$3"
  shift 3
  local validate=()
  [[ -n "$etag" && "$etag" != "null" ]] && validate=(-H "If-Range: \"$etag\"")
  service_curl \
    -f --retry 3 --retry-connrefused --retry-delay 2 \
    -C - "${validate[@]}" "$@" -o "$out" "$url"
}

_download_terminal() {
  local url="$1" out="$2" etag="$3"
  log "downloading to $out"
  _curl_download "$url" "$out" "$etag" --progress-bar
}

_download_quiet() {
  local url="$1" out="$2" etag="$3"
  _curl_download "$url" "$out" "$etag" --silent --show-error
}

# Graphical progress. curl runs in the background and the dialog is driven from
# the size of the partial file, which also works for the streamed zips that have
# no Content-Length.
_download_zenity() {
  local url="$1" out="$2" etag="$3" title="$4" expected="${5:-0}"

  # A private directory for the fifo: mktemp -u then mkfifo races with anything
  # else that could claim the name in between.
  local pipedir pipe curl_pid zen_pid status=0
  pipedir="$(mktemp -d)"
  pipe="$pipedir/progress"
  mkfifo "$pipe"

  local mode=(--auto-close)
  ((expected <= 0)) && mode+=(--pulsate)

  zenity --progress --title="GOTG" \
    --text="Downloading $title…" "${mode[@]}" <"$pipe" &
  zen_pid=$!

  exec 9>"$pipe"
  _curl_download "$url" "$out" "$etag" --silent --show-error &
  curl_pid=$!

  local size pct=0
  while kill -0 "$curl_pid" 2>/dev/null; do
    # The user closed or cancelled the dialog: stop the transfer, keep the
    # partial file so the next attempt resumes.
    if ! kill -0 "$zen_pid" 2>/dev/null; then
      kill "$curl_pid" 2>/dev/null || true
      wait "$curl_pid" 2>/dev/null || true
      exec 9>&-
      rm -rf "$pipedir"
      # Could be the user cancelling, or zenity failing to start at all; say so
      # rather than asserting an intent we cannot observe.
      die "download stopped: the progress dialog closed (cancelled, or zenity could not run)"
    fi
    if [[ "$expected" -gt 0 ]]; then
      size="$(stat -c '%s' "$out" 2>/dev/null || echo 0)"
      pct=$((size * 100 / expected))
      ((pct > 99)) && pct=99
      printf '%s\n# %s of %s\n' "$pct" "$(human_size "$size")" "$(human_size "$expected")" >&9
    else
      size="$(stat -c '%s' "$out" 2>/dev/null || echo 0)"
      printf '# %s downloaded\n' "$(human_size "$size")" >&9
    fi
    sleep "$PROGRESS_TICK"
  done

  wait "$curl_pid" || status=$?
  # Only claim completion if it actually completed; jumping the bar to 100% on a
  # failed transfer tells the user the opposite of what happened.
  if [[ "$status" -eq 0 ]]; then
    printf '100\n' >&9 || true
  else
    printf '# download failed\n' >&9 || true
  fi
  exec 9>&-
  rm -rf "$pipedir"
  wait "$zen_pid" 2>/dev/null || true
  return "$status"
}

# Pick the progress style that suits where we are running.
_download_with_progress() {
  local url="$1" out="$2" etag="$3" title="$4" expected="$5"
  if is_tty; then
    _download_terminal "$url" "$out" "$etag"
  elif has_display && command -v zenity >/dev/null 2>&1; then
    _download_zenity "$url" "$out" "$etag" "$title" "$expected"
  else
    _download_quiet "$url" "$out" "$etag"
  fi
}

# A member's server path, when it lies inside the library mounted here
# (GOTG_LIBRARY_MOUNT) and is a plain file. Anything else is a download.
_library_member() {
  local path="$1" mount="${GOTG_LIBRARY_MOUNT:-}"
  [[ -n "$mount" && -n "$path" ]] || return 1
  mount="${mount%/}"
  case "$path" in
    "$mount"/?*) ;;
    *) return 1 ;;
  esac
  [[ "$path" != *"/../"* && "$path" != *"/./"* ]] || return 1
  [[ -f "$path" && ! -L "$path" ]] || return 1
  printf '%s' "$path"
}

# The importer writes bare-hex sidecars, so the manifest digest and the sidecar
# agree; either is enough to catch a truncated or corrupted transfer.
_verify_checksum() {
  local file="$1" expected="$2"
  [[ -n "$expected" && "$expected" != "null" ]] || return 0
  local actual
  actual="$(sha256sum "$file" | cut -d' ' -f1)"
  if [[ "$actual" != "$expected" ]]; then
    rm -f "$file"
    die "checksum mismatch for $(basename "$file") — deleted the download.
     expected $expected
     got      $actual"
  fi
  log "checksum ok"
}

# Move a completed download into place. Staging lives under the games directory
# so this is a rename on one filesystem, never a copy.
_install_file() {
  local staged="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  mv -f "$staged" "$dest"
}

# Download one game if it is not already here. Returns 0 when the game is ready.
#
# An entry is a list of raw member files; each downloads with resume and its
# own checksum. What happens after the last member depends on the handler:
# a single file moves into place, a tree stays a tree, and everything else is
# a recipe the game's environment carries — unrar, convert, whatever this
# platform's raw sources need. The raw members are deleted once the refined
# artifact exists; they are re-downloadable, it is deterministic.
download_game() {
  local game="$1"
  local id platform handler title dest staged
  id="$(manifest_field "$game" id)"
  platform="$(manifest_field "$game" platform)"
  handler="$(manifest_field "$game" handler)"
  title="$(manifest_field "$game" title)"
  : "${title:=$id}"

  validate_id "$id"
  validate_platform "$platform"

  if game_is_installed "$game"; then
    _warn_without_extras "$game"
    return 0
  fi
  dest="$(game_local_path "$game")"

  mkdir -p "$GOTG_PARTIAL_DIR"
  staged="$(download_partial_path "$id")"

  # Two fetches of one game share a staging directory, so a terminal
  # `gotg install` racing a Steam launch of the same title would interleave.
  # The lock makes the second wait and then see the finished install.
  mkdir -p "$GOTG_STATE_DIR/locks"
  exec 8>"$GOTG_STATE_DIR/locks/$id.lock"
  if ! flock -w 3600 8; then
    die "timed out waiting for another gotg process to finish downloading $id"
  fi
  if game_is_installed "$game"; then
    exec 8>&-
    return 0
  fi

  service_have || die "no service configured — run: gotg login"

  # With the library mounted, a cache fetched before it was carries no
  # paths: one refresh, so the copy below has somewhere to copy from.
  if [[ -n "${GOTG_LIBRARY_MOUNT:-}" ]] && ! jq -e '[.files[]?.path] | any' <<<"$game" >/dev/null 2>&1; then
    if manifest_refresh; then
      game="$(manifest_find "$platform/$id")"
    fi
  fi

  local total count
  total="$(jq -r '[.files[].size_bytes] | add' <<<"$game")"
  count="$(jq -r '.files | length' <<<"$game")"
  log "fetching $title ($(human_size "$total"), $count file(s))"

  local files_base
  files_base="$(manifest_files_pick)"

  local name size sha out url encoded server_path local_src
  while IFS=$'\t' read -r name size sha encoded server_path; do
    validate_filename "$name"
    [[ "$sha" == "null" || "$sha" =~ ^[0-9a-f]{64}$ ]] ||
      die "invalid sha256 in catalog for $name"
    out="$staged/$name"
    mkdir -p "$(dirname "$out")"
    # The library is mounted here: the member is copied from it, and verified
    # exactly as a download would be — the catalog's hash is the contract.
    if local_src="$(_library_member "$server_path")"; then
      log "copying $name from the library at $GOTG_LIBRARY_MOUNT"
      cp --reflink=auto -- "$local_src" "$out" || die "could not copy $name from $local_src"
      (_verify_checksum "$out" "$sha") || die "the library's copy of $name does not match the catalog"
      continue
    fi
    url="$files_base/games/$platform/$id/$encoded"
    local fetched="" dl_status attempt
    for attempt in 1 2 3; do
      if _download_with_progress "$url" "$out" "$sha" "$title: $name" "${size:-0}"; then
        # Verified inside the loop, in a subshell so its die ends the attempt
        # rather than the command: a poisoned partial resumes into a file
        # that hashes wrong — curl even calls a 416 on an oversized offset
        # success — and verify already deletes it, which is exactly the reset
        # the next attempt needs.
        if (_verify_checksum "$out" "$sha"); then
          fetched=1
          break
        fi
      else
        dl_status=$?
        # Exit 33: the server refused to resume these bytes; keeping them
        # would wedge every retry. Everything else — a reset mid-transfer, a
        # timeout — keeps the partial and resumes from where it stopped,
        # which is the difference between finishing the last megabyte and
        # paying for the whole file again.
        [[ "$dl_status" -eq 33 ]] && rm -f "$out"
      fi
      # The byte host is the catalog's to name and can move between reads —
      # a VPN-fronted files host changes address and port on reconnect, which
      # is also the likeliest source of the reset being retried here.
      manifest_refresh || true
      files_base="$(manifest_files_pick)"
      url="$files_base/games/$platform/$id/$encoded"
      [[ "$attempt" -lt 3 ]] && warn "download interrupted — retrying (attempt $((attempt + 1)) of 3)"
    done
    [[ -n "$fetched" ]] ||
      die "download failed for $id (partials kept at $staged; run again to resume)"
  done < <(jq -r '.files[] | [.name, .size_bytes, (.sha256 // "null"),
    (.name | split("/") | map(@uri) | join("/")), (.path // "")] | @tsv' <<<"$game")

  case "$handler" in
    single_file | no_intro_set)
      # An `unzip` override means this game's own environment carries the
      # recipe that unpacks it — the same pipeline every processed source
      # goes through — and so do attached updates or DLC, which only a recipe
      # can set beside the game. Everything else is placement, not processing.
      if [[ "$(override_field "$game" unzip)" == "true" ]] || game_has_extras "$game"; then
        _run_recipe "$game" "$handler" "$staged" "$dest"
      else
        local member
        member="$staged/$(game_base_member "$game")"
        _install_file "$member" "$dest"
      fi
      ;;
    wiiu_decrypted)
      # Placement too: the tree is already what the emulator reads, so no
      # environment (and no built recipe) is required to put it in place.
      mkdir -p "$(dirname "$dest")"
      rm -rf "$dest"
      mv "$staged" "$dest"
      ;;
    *)
      _run_recipe "$game" "$handler" "$staged" "$dest"
      ;;
  esac
  rm -rf "$staged"

  exec 8>&-
  log "installed $(game_installed_path "$game" || printf '%s' "$dest")"
}

# An install from before the catalog attached updates or DLC to this game
# runs as it is; fetching them means reinstalling, which is not done behind
# anyone's back.
_warn_without_extras() {
  local game="$1" legacy
  legacy="$(game_legacy_path "$game" "$(game_games_dir "$game")")" || return 0
  [[ -f "$legacy" ]] || return 0
  warn "$(manifest_field "$game" id) is installed without its updates and DLC — uninstall and install again to fetch them"
}

# The environment owns the recipe and its tools; the catalog only said what
# the source is. The refined artifact keeps the id and the recipe picks the
# extension, which game_installed_path resolves by glob.
_run_recipe() {
  local game="$1" handler="$2" staged="$3" dest="$4"
  local attr recipe
  attr="$(env_attr "$game")"
  recipe="$GOTG_ROOTS_DIR/$attr/bin/gotg-recipe"
  # An environment that gains a recipe leaves every root built before it
  # without one, and env_ensure only builds what is missing altogether — so the
  # game would fail here on exactly the machines that already had it working.
  # Rebuild once before believing the root, which is the trade install makes.
  [[ -x "$recipe" ]] || env_refresh "$attr" || true
  [[ -x "$recipe" ]] ||
    die "$attr has no recipe for '$handler' built yet — run: gotg install $(manifest_field "$game" id)"
  jq -e --arg h "$handler" '.handlers | index($h)' \
    "$GOTG_ROOTS_DIR/$attr/share/gotg/recipe.json" >/dev/null 2>&1 ||
    die "$attr declares no recipe for '$handler'"
  log "processing $(manifest_field "$game" id) ($handler)"
  mkdir -p "$(dirname "$dest")"
  "$recipe" "$handler" "$staged" "$dest" ||
    die "the $handler recipe failed for $(manifest_field "$game" id)"
}

cmd_download() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg download <id>"
  manifest_ensure
  local game
  game="$(manifest_find "$want")"
  local here
  if here="$(game_installed_path "$game")"; then
    log "already installed: $here"
    _warn_without_extras "$game"
    return 0
  fi
  download_game "$game"
}
