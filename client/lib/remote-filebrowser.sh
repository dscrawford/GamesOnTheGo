# shellcheck shell=bash
# The blob store on the File Browser that already serves the library.
#
# No new infrastructure, no second set of credentials, no extra permissions: the
# same account, writing under the same hidden .gotg directory the catalog lives
# in. Matched to filebrowser's http/resource.go, where the details are less
# obvious than the verbs suggest:
#
#   - an upload is a raw body, not multipart, and a success is 200, not 201
#   - without ?override=true, writing over an existing file is a 409
#   - writeFile does MkdirAll on the parent, so nothing has to create a
#     directory first — there is no mkdir in this API to call anyway
#   - /api/tus exists for resumable uploads; these bundles are kilobytes, so
#     using it would be a lot of machinery bought for nothing

# /api/resources carries the metadata and the write verbs; the bytes of a file
# come from /api/raw, the same endpoint the game downloads use.
fb_url() {
  local key="$1"
  printf '%s/api/resources%s' "$GOTG_SERVER" "$(url_encode_path "$(saves_root)/$key")"
}

fb_raw_url() {
  api_raw_url "$(saves_root)/$1"
}

# One login per operation would be three logins for one push. The token is
# fetched once per process and kept in a shell variable that is never exported.
FB_TOKEN=""
fb_token() {
  [[ -n "$FB_TOKEN" ]] || {
    config_load
    FB_TOKEN="$(api_login)"
  }
  printf '%s' "$FB_TOKEN"
}

fb_curl() {
  api_curl "$(fb_token)" -sS --connect-timeout 10 --max-time "${GOTG_SAVES_TIMEOUT:-120}" "$@"
}

# Upload beside the final name and rename into place, so a reader never sees a
# half-written bundle — the same rule the downloader already follows locally.
fb_blob_put() {
  local file="$1" key="$2" http
  local staging="$key.part"

  http="$(fb_curl -o /dev/null -w '%{http_code}' \
    --data-binary "@$file" \
    -X POST "$(fb_url "$staging")?override=true")" ||
    die "could not reach $GOTG_SERVER while uploading $key"
  [[ "$http" == "200" ]] || die "upload of $key failed (HTTP $http)"

  local dest
  dest="$(url_encode_path "$(saves_root)/$key")"
  http="$(fb_curl -o /dev/null -w '%{http_code}' \
    -X PATCH "$(fb_url "$staging")?action=rename&destination=$dest&override=true")" ||
    die "could not reach $GOTG_SERVER while publishing $key"
  case "$http" in
    200 | 204) ;;
    *) die "publishing $key failed (HTTP $http)" ;;
  esac
}

fb_blob_get() {
  local key="$1" file="$2" http
  mkdir -p "$(dirname "$file")"
  # Written to .part and moved, so a failed or truncated download cannot be
  # mistaken for a complete one. The size cap is enforced by curl itself rather
  # than after the fact, so a hostile remote cannot fill the disk first.
  http="$(fb_curl -o "$file.part" -w '%{http_code}' \
    --max-filesize "$(saves_max_bytes)" \
    "$(fb_raw_url "$key")")" || {
    rm -f "$file.part"
    die "could not download $key from $GOTG_SERVER (over ${GOTG_SAVES_MAX_BYTES:-} bytes, or unreachable)"
  }
  if [[ "$http" != "200" ]]; then
    rm -f "$file.part"
    return 1
  fi
  mv "$file.part" "$file"
}

# "<bytes> <sha256>", the checksum computed by the server so that a remote
# bundle can be verified without downloading it. Returns 1 when absent, which is
# the ordinary "nothing has been pushed yet" case and not an error.
fb_blob_stat() {
  local key="$1" body http
  body="$(fb_curl -w '\n%{http_code}' "$(fb_url "$key")?checksum=sha256")" ||
    die "could not reach $GOTG_SERVER"
  http="${body##*$'\n'}"
  body="${body%$'\n'*}"
  [[ "$http" == "200" ]] || return 1
  jq -r '"\(.size) \(.checksums.sha256 // "")"' <<<"$body"
}

fb_blob_list() {
  local prefix="$1" body http
  body="$(fb_curl -w '\n%{http_code}' "$(fb_url "$prefix")")" ||
    die "could not reach $GOTG_SERVER"
  http="${body##*$'\n'}"
  body="${body%$'\n'*}"
  # A directory that was never created is an empty listing, not a failure.
  [[ "$http" == "200" ]] || return 0
  jq -r '.items[]? | select(.isDir | not) | .name' <<<"$body" | sort
}

fb_blob_delete() {
  local key="$1" http
  http="$(fb_curl -o /dev/null -w '%{http_code}' -X DELETE "$(fb_url "$key")")" ||
    die "could not reach $GOTG_SERVER"
  case "$http" in
    200 | 204 | 404) return 0 ;;
    *) die "deleting $key failed (HTTP $http)" ;;
  esac
}
