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

  local total count
  total="$(jq -r '[.files[].size_bytes] | add' <<<"$game")"
  count="$(jq -r '.files | length' <<<"$game")"
  log "fetching $title ($(human_size "$total"), $count file(s))"

  local name size sha out url encoded
  while IFS=$'\t' read -r name size sha encoded; do
    validate_filename "$name"
    [[ "$sha" == "null" || "$sha" =~ ^[0-9a-f]{64}$ ]] ||
      die "invalid sha256 in catalog for $name"
    out="$staged/$name"
    mkdir -p "$(dirname "$out")"
    url="$(service_url)/games/$platform/$id/$encoded"
    if ! _download_with_progress "$url" "$out" "$sha" "$title: $name" "${size:-0}"; then
      # A stale partial whose bytes the server no longer has curl-resumes into
      # a 200, which curl rejects (exit 33) and would wedge every retry. Drop
      # the partial and try once from zero before giving up.
      if [[ -e "$out" ]]; then
        rm -f "$out"
        _download_with_progress "$url" "$out" "$sha" "$title: $name" "${size:-0}" ||
          die "download failed for $id (partials kept at $staged; run again to resume)"
      else
        die "download failed for $id (partials kept at $staged; run again to resume)"
      fi
    fi
    _verify_checksum "$out" "$sha"
  done < <(jq -r '.files[] | [.name, .size_bytes, (.sha256 // "null"),
    (.name | split("/") | map(@uri) | join("/"))] | @tsv' <<<"$game")

  case "$handler" in
    single_file | no_intro_set)
      # An `unzip` override means this game's own environment carries the
      # recipe that unpacks it — the same pipeline every processed source
      # goes through. Everything else is placement, not processing.
      if [[ "$(override_field "$game" unzip)" == "true" ]]; then
        _run_recipe "$game" "$handler" "$staged" "$dest"
      else
        local member
        member="$staged/$(jq -r '.files[0].name' <<<"$game")"
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

# The environment owns the recipe and its tools; the catalog only said what
# the source is. The refined artifact keeps the id and the recipe picks the
# extension, which game_installed_path resolves by glob.
_run_recipe() {
  local game="$1" handler="$2" staged="$3" dest="$4"
  local attr recipe
  attr="$(env_attr "$game")"
  recipe="$GOTG_ROOTS_DIR/$attr/bin/gotg-recipe"
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
  if game_is_installed "$game"; then
    log "already installed: $(game_local_path "$game")"
    return 0
  fi
  download_game "$game"
}
