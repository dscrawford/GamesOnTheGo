# shellcheck shell=bash
# The saves store, as the client sees it: save and retrieve, nothing else.
#
#   store_save     <attr> <bundle> [parent] [force]   -> meta JSON on stdout
#   store_retrieve <attr> <out-file>                  -> "<generation> <hash>"
#   store_meta     <attr>                             -> meta JSON on stdout
#
# All three speak to the one GOTG service — the same url and token that carry
# artwork requests, from the same api.json — so a machine is configured once.
# Conflicts are the server's to decide: a save carries the hash of the
# generation this machine descends from, and the service either advances the
# head atomically or answers 409 with what is actually there. store_save
# returns 2 for that refusal, with the head's metadata on stdout, and the
# caller's whole job is to repeat it to a person. Retention is also the
# server's, which is why nothing here can delete anything.
#
# Return codes are the vocabulary: 0 is an answer, 1 is "nothing there yet" —
# the ordinary first-run case, never an error — and 2 is "could not ask".
# Anything unexpected dies here, naming the service.

saves_api_file() { printf '%s' "${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}"; }

saves_have_remote() {
  local file
  file="$(saves_api_file)"
  [[ -f "$file" ]] || return 1
  jq -e '.url and .token' "$file" >/dev/null 2>&1
}

saves_api_url() {
  local url
  url="$(jq -r '.url // empty' "$(saves_api_file)")"
  printf '%s' "${url%/}"
}

# The token authenticates to a service on the public internet, so the file is
# held to the same permission rule as every other secret here, and the token
# reaches curl on stdin — /proc/<pid>/cmdline is world-readable, and argv is
# not a place for credentials.
saves_api_curl() {
  config_check_perms "$(saves_api_file)"
  jq -r '"header = \"Authorization: Bearer \(.token)\""' "$(saves_api_file)" |
    curl --config - -sS --connect-timeout 10 --max-time "${GOTG_API_TIMEOUT:-120}" "$@"
}

# .save(): publish this bundle as the next generation.
#
# 0 with the published metadata; 2 with the current head's metadata when the
# remote has moved on and this push was not forced — the caller formats that
# refusal, because only it knows what to tell a person to do about it.
store_save() {
  local attr="$1" bundle="$2" parent="${3:-}" force="${4:-}"
  validate_attr "$attr"
  local query="" out http
  [[ "$force" == "force" ]] && query="?force=1"
  out="$(saves_tmp)/store-save.json"

  http="$(saves_api_curl -X PUT --data-binary "@$bundle" \
    -H "X-Gotg-Parent: $parent" -H "X-Gotg-Device: $(device_id)" \
    -o "$out" -w '%{http_code}' \
    "$(saves_api_url)/saves/$attr$query")" ||
    die "could not reach the GOTG service at $(saves_api_url)"

  case "$http" in
    200) cat "$out" ;;
    409)
      cat "$out"
      return 2
      ;;
    401) die "the GOTG service refused the token — check $(saves_api_file)" ;;
    *) die "the GOTG service answered HTTP $http pushing $attr: $(jq -r '.error // "no detail"' "$out" 2>/dev/null)" ;;
  esac
}

# .retrieve(): the current generation's bundle, and which generation it is.
store_retrieve() {
  local attr="$1" out="$2"
  validate_attr "$attr"
  local headers http
  headers="$(saves_tmp)/retrieve-headers"

  # The size cap is handed to curl rather than checked afterwards, so a
  # hostile or broken service cannot fill the disk before being refused.
  http="$(saves_api_curl -D "$headers" -o "$out.part" -w '%{http_code}' \
    --max-filesize "$(saves_max_bytes)" \
    "$(saves_api_url)/saves/$attr")" || {
    rm -f "$out.part"
    return 2
  }

  case "$http" in
    200) ;;
    404)
      rm -f "$out.part"
      return 1
      ;;
    401) die "the GOTG service refused the token — check $(saves_api_file)" ;;
    *)
      rm -f "$out.part"
      return 2
      ;;
  esac

  mv "$out.part" "$out"
  # The headers say which generation these bytes are, so the journal can be
  # set from one request. The hash is verified against the bytes by the
  # caller before anything is extracted — the header is a claim, not proof.
  awk 'tolower($1) == "x-gotg-generation:" { gen = $2 }
       tolower($1) == "x-gotg-hash:" { hash = $2 }
       END { gsub("\r", "", gen); gsub("\r", "", hash); print gen, hash }' "$headers"
}

# What is current, without moving the bytes. For status, and for deciding
# whether a pull would change anything.
store_meta() {
  local attr="$1"
  validate_attr "$attr"
  local out http
  out="$(saves_tmp)/store-meta.json"

  http="$(saves_api_curl -o "$out" -w '%{http_code}' \
    "$(saves_api_url)/saves/$attr/meta")" || return 2

  case "$http" in
    200) cat "$out" ;;
    404) return 1 ;;
    401) die "the GOTG service refused the token — check $(saves_api_file)" ;;
    *) return 2 ;;
  esac
}
