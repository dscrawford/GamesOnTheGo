# shellcheck shell=bash
# The blob store the saves live in, and nothing else.
#
# Five operations over opaque keys. Everything above this line speaks in keys —
# "env-n64/gen/000042-3f9a1c2b4d5e.tar.zst" — and never in URLs, remote syntax
# or anything else that would tie the save logic to one backend. That is the
# whole point: the second backend should be a new file next to this one rather
# than a rewrite of the thing that decides which save wins.
#
#   blob_put    <local-file> <key>   upload, atomically as seen by a reader
#   blob_get    <key> <local-file>   download, atomically as seen locally
#   blob_stat   <key>                "<bytes> <sha256-or-empty>"; 1 if absent
#   blob_list   <prefix>             one name per line, sorted
#   blob_delete <key>                0 when it was already gone
#
# A key becomes a URL, a path and an argv entry, and half of them come back from
# a listing the server controls, so every one is checked first — there is no
# path through this file that skips validate_blob_key.

remote_backend() {
  local backend="${GOTG_SAVES_BACKEND:-}"
  [[ -z "$backend" ]] && backend="$(config_get saves_backend 2>/dev/null || true)"
  printf '%s' "${backend:-filebrowser}"
}

remote_require_backend() {
  local backend
  backend="$(remote_backend)"
  case "$backend" in
    filebrowser | rclone) printf '%s' "$backend" ;;
    *) die "unknown saves backend '$backend' — set it with: gotg saves setup" ;;
  esac
}

# Dispatch to <verb>_<backend>. Written out rather than built from a string so
# that shellcheck can see the call and a typo is a missing function, not a
# silent no-op.
blob_put() {
  local file="$1" key="$2"
  validate_blob_key "$key"
  [[ -f "$file" ]] || die "nothing to upload at $file"
  case "$(remote_require_backend)" in
    filebrowser) fb_blob_put "$file" "$key" ;;
    rclone) rclone_blob_put "$file" "$key" ;;
  esac
}

blob_get() {
  local key="$1" file="$2"
  validate_blob_key "$key"
  case "$(remote_require_backend)" in
    filebrowser) fb_blob_get "$key" "$file" ;;
    rclone) rclone_blob_get "$key" "$file" ;;
  esac
}

blob_stat() {
  local key="$1"
  validate_blob_key "$key"
  case "$(remote_require_backend)" in
    filebrowser) fb_blob_stat "$key" ;;
    rclone) rclone_blob_stat "$key" ;;
  esac
}

# The prefix is a directory of keys, so it is an attribute optionally followed
# by /gen — never an arbitrary path.
blob_list() {
  local prefix="$1"
  local attr="${prefix%%/*}"
  validate_attr "$attr"
  case "$prefix" in
    "$attr" | "$attr/gen") ;;
    *) die "invalid remote prefix: $prefix" ;;
  esac
  case "$(remote_require_backend)" in
    filebrowser) fb_blob_list "$prefix" ;;
    rclone) rclone_blob_list "$prefix" ;;
  esac
}

blob_delete() {
  local key="$1"
  validate_blob_key "$key"
  case "$(remote_require_backend)" in
    filebrowser) fb_blob_delete "$key" ;;
    rclone) rclone_blob_delete "$key" ;;
  esac
}

# Where the saves live on the remote. Under the same hidden .gotg directory the
# catalog already uses, so it is the same account with the same scope and the
# importer's staging cleanup — which only ever removes .gotg-extract-* and
# .gotg-zip-* directories — cannot reach it.
saves_root() {
  local root
  root="$(config_get saves_root 2>/dev/null || true)"
  [[ -n "$root" ]] || root="${GOTG_REMOTE_ROOT%/}/.gotg/saves"
  validate_remote_path "$root"
  printf '%s' "${root%/}"
}
