# shellcheck shell=bash
# Fetching a game from the server.
#
# Downloads land in a staging directory first and are only moved into place once
# complete and verified, so an interrupted transfer can never look like an
# installed game — which matters when the next thing to touch it is Steam.

# Poll interval for the graphical progress dialog.
PROGRESS_TICK="${GOTG_PROGRESS_TICK:-0.5}"

download_partial_path() {
  local id="$1" type="$2"
  if [[ "$type" == "dir" ]]; then
    printf '%s/%s.zip' "$GOTG_PARTIAL_DIR" "$id"
  else
    printf '%s/%s' "$GOTG_PARTIAL_DIR" "$id"
  fi
}

# curl with resume. Directory downloads are zipped on the fly, so their byte
# offsets are not stable across requests and resuming would corrupt them.
_curl_download() {
  local url="$1" out="$2" type="$3"
  shift 3
  local resume=(-C -)
  [[ "$type" == "dir" ]] && resume=()
  curl -fL --retry 3 --retry-connrefused --retry-delay 2 \
    --connect-timeout 15 \
    "${resume[@]}" "$@" -o "$out" "$url"
}

_download_terminal() {
  local url="$1" out="$2" type="$3"
  log "downloading to $out"
  _curl_download "$url" "$out" "$type" --progress-bar
}

_download_quiet() {
  local url="$1" out="$2" type="$3"
  _curl_download "$url" "$out" "$type" --silent --show-error
}

# Graphical progress. curl runs in the background and the dialog is driven from
# the size of the partial file, which also works for the streamed zips that have
# no Content-Length.
_download_zenity() {
  local url="$1" out="$2" type="$3" title="$4" expected="${5:-0}"

  # A private directory for the fifo: mktemp -u then mkfifo races with anything
  # else that could claim the name in between.
  local pipedir pipe curl_pid zen_pid status=0
  pipedir="$(mktemp -d)"
  pipe="$pipedir/progress"
  mkfifo "$pipe"

  # Without a known total there is no percentage to show, which is the normal
  # case for a directory the server zips as it streams.
  local mode=(--auto-close)
  if [[ "$type" == "dir" ]] || ((expected <= 0)); then
    mode+=(--pulsate)
  fi

  zenity --progress --title="GOTG" \
    --text="Downloading $title…" "${mode[@]}" <"$pipe" &
  zen_pid=$!

  exec 9>"$pipe"
  _curl_download "$url" "$out" "$type" --silent --show-error &
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
  local url="$1" out="$2" type="$3" title="$4" expected="$5"
  if is_tty; then
    _download_terminal "$url" "$out" "$type"
  elif has_display && command -v zenity >/dev/null 2>&1; then
    _download_zenity "$url" "$out" "$type" "$title" "$expected"
  else
    _download_quiet "$url" "$out" "$type"
  fi
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

# A zipped ROM unpacked into its own directory, for emulators that cannot read
# an archive (the 2s2h port extracts assets from a bare .z64, for instance).
_install_zipped_rom() {
  local staged_zip="$1" dest="$2"
  local stage="$GOTG_PARTIAL_DIR/unzip-$$"
  rm -rf "$stage"
  mkdir -p "$stage"

  unzip -q "$staged_zip" -d "$stage" || {
    rm -rf "$stage"
    die "could not unzip $(basename "$staged_zip")"
  }

  mkdir -p "$(dirname "$dest")"
  rm -rf "$dest"
  mv "$stage" "$dest"
  rm -f "$staged_zip"
}

_install_dir_zip() {
  local staged_zip="$1" dest="$2"
  local stage="$GOTG_PARTIAL_DIR/stage-$$"
  rm -rf "$stage"
  mkdir -p "$stage"

  unzip -q "$staged_zip" -d "$stage" || {
    rm -rf "$stage"
    die "could not unzip $(basename "$staged_zip")"
  }

  mkdir -p "$(dirname "$dest")"
  rm -rf "$dest"
  # File Browser zips a folder with its own name at the root; unwrap that so the
  # layout matches what the server has.
  local entries=("$stage"/*)
  if [[ ${#entries[@]} -eq 1 && -d "${entries[0]}" ]]; then
    mv "${entries[0]}" "$dest"
  else
    mv "$stage" "$dest"
  fi
  rm -rf "$stage" "$staged_zip"
}

# Download one game if it is not already here. Returns 0 when the game is ready.
download_game() {
  local game="$1"
  local id remote type size sha title dest staged
  id="$(manifest_field "$game" id)"
  remote="$(manifest_field "$game" path)"
  type="$(manifest_field "$game" type)"
  size="$(manifest_field "$game" size_bytes)"
  sha="$(manifest_field "$game" sha256)"
  title="$(manifest_field "$game" title)"
  : "${title:=$id}"

  validate_id "$id"
  validate_remote_path "$remote"

  dest="$(game_local_path "$game")"
  if [[ -e "$dest" ]]; then
    return 0
  fi

  mkdir -p "$GOTG_PARTIAL_DIR"
  staged="$(download_partial_path "$id" "$type")"

  # Two fetches of one game share a staging file, so a terminal `gotg install`
  # racing a Steam launch of the same title would interleave two curls into it.
  # The lock makes the second wait and then see the finished install. It lives in
  # the state directory, not next to the downloads, so it is not mistaken for a
  # leftover partial file.
  mkdir -p "$GOTG_STATE_DIR/locks"
  exec 8>"$GOTG_STATE_DIR/locks/$id.lock"
  if ! flock -w 3600 8; then
    die "timed out waiting for another gotg process to finish downloading $id"
  fi
  if [[ -e "$dest" ]]; then
    exec 8>&-
    return 0
  fi

  config_load
  local token url
  token="$(api_login)"
  url="$(api_raw_url "$remote" "$token" "$type")"

  log "fetching $title ($(human_size "$size"))"
  _download_with_progress "$url" "$staged" "$type" "$title" "${size:-0}" ||
    die "download failed for $id (partial kept at $staged; run again to resume)"

  if [[ "$type" == "dir" ]]; then
    _install_dir_zip "$staged" "$dest"
  else
    _verify_checksum "$staged" "$sha"
    if [[ "$(override_field "$game" unzip)" == "true" ]]; then
      # Verified as downloaded, then unpacked: the checksum still covers what
      # came off the server.
      _install_zipped_rom "$staged" "$dest"
    else
      _install_file "$staged" "$dest"
    fi
  fi

  exec 8>&-
  log "installed $dest"
}

cmd_download() {
  local want="${1:-}"
  [[ -n "$want" ]] || die "usage: gotg download <id>"
  manifest_ensure
  local game
  game="$(manifest_find "$want")"
  if game_is_installed "$game"; then
    log "already installed: $(game_local_path "$game")"
    return 0
  fi
  download_game "$game"
}
