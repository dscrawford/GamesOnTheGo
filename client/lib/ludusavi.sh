# shellcheck shell=bash
# Driving Ludusavi, which does the moving of saves.
#
# Ludusavi already solves the parts of this that are the same for everybody:
# walking a save set, noticing which files changed, keeping the last few copies,
# and driving rclone to a remote. What it does not know is anything about gotg —
# which environment a game belongs to, where that environment keeps its saves,
# or what counts as a save there. That knowledge lives in the saves.json each
# environment's derivation emits, so this generates Ludusavi's entire config
# from those manifests on every run: an environment is a "custom game", its
# globs are that game's files, its excludes are ignored paths.
#
# Generated wholesale, never edited in place. Ludusavi's config is one document
# with one key per section, so appending a section that already exists makes the
# whole file unparseable — and a half-updated config is worse than none.
#
# The file is named .yaml and holds JSON, which is deliberate: YAML is a
# superset of JSON, so jq can write it, and no path with a space or a quote in
# it has to survive hand-rolled YAML quoting.
#
# Its config lives under gotg's state directory, not the user's. Ludusavi is a
# program people run for their own games, and silently rewriting the config of
# an application someone else installed would be an unpleasant surprise.

LUD_REMOTE="gotg-saves"

lud_home() { printf '%s/ludusavi' "$GOTG_STATE_DIR"; }
lud_config_file() { printf '%s/ludusavi/config.yaml' "$(lud_home)"; }
lud_backup_dir() { printf '%s/ludusavi-backup' "$GOTG_STATE_DIR"; }
lud_remote_file() { printf '%s/saves-remote.json' "$GOTG_CONFIG_DIR"; }

lud_have_remote() { [[ -f "$(lud_remote_file)" ]]; }

# The rclone remote, written from the one file that holds the credentials.
#
# Two kinds, because they are the two real ones: a WebDAV server, and a
# directory — which covers a mounted NAS as well as it covers a test.
lud_rclone_conf() {
  local spec out kind
  spec="$(lud_remote_file)"
  out="$(lud_home)/rclone.conf"
  [[ -f "$spec" ]] || return 1

  mkdir -p "$(lud_home)"
  chmod 700 "$(lud_home)"
  kind="$(jq -r '.type // "webdav"' "$spec")"

  case "$kind" in
    alias)
      local path
      path="$(jq -r '.path' "$spec")"
      [[ -n "$path" && "$path" != "null" ]] || die "$spec: an alias remote needs a path"
      printf '[%s]\ntype = alias\nremote = %s\n' "$LUD_REMOTE" "$path" >"$out"
      ;;
    webdav)
      local url user pass
      url="$(jq -r '.url // empty' "$spec")"
      user="$(jq -r '.username // empty' "$spec")"
      pass="$(jq -r '.password // empty' "$spec")"
      [[ -n "$url" ]] || die "$spec: a webdav remote needs a url"
      # rclone stores an obscured password, not a plain one. This is not
      # encryption and is not treated as any — the file is 0600 for the same
      # reason the credentials file is.
      printf '[%s]\ntype = webdav\nurl = %s\nvendor = other\nuser = %s\npass = %s\n' \
        "$LUD_REMOTE" "$url" "$user" "$(rclone obscure "$pass")" >"$out"
      ;;
    *)
      die "$spec: unknown remote type '$kind' (webdav, alias)"
      ;;
  esac

  chmod 600 "$out"
  printf '%s' "$out"
}

# One custom game per environment, from that environment's own manifest.
#
# `redirects` is how a save reaches a machine whose directories are not laid out
# identically: Ludusavi records the absolute path a file came from, and $HOME is
# not the same everywhere.
lud_write_config() {
  local redirect_from="${LUD_REDIRECT_FROM:-}" redirect_to="${LUD_REDIRECT_TO:-}"
  local games='[]' ignored='[]' attr manifest state

  for attr in "$@"; do
    manifest="$(saves_manifest "$attr")"
    state="$(env_state_dir "$attr")"
    # `saves/**` means "everything under saves" in the manifest, as it does to
    # bash and to tar. To Ludusavi `**` means "at least one directory down", so
    # the same pattern would quietly skip every file sitting directly in
    # saves/ — the memory saves, in other words. A bare directory is what
    # Ludusavi recurses into, so that suffix is translated away rather than
    # passed through.
    games="$(jq -c --arg name "$attr" --arg state "$state" --argjson m "$manifest" \
      '. + [{name: $name, integration: "override",
             files: (($m.saves // []) | map($state + "/" + (. | sub("/[*][*]$"; "")))),
             registry: [], installDir: [], winePrefix: []}]' <<<"$games")"
    ignored="$(jq -c --arg state "$state" --argjson m "$manifest" \
      '. + (($m.excludes // []) | map($state + "/" + .))' <<<"$ignored")"
  done

  local redirects='[]'
  if [[ -n "$redirect_from" && -n "$redirect_to" ]]; then
    redirects="$(jq -nc --arg from "$redirect_from" --arg to "$redirect_to" \
      '[{kind: "restore", source: $from, target: $to}]')"
  fi

  local rclone_conf=""
  lud_have_remote && rclone_conf="$(lud_rclone_conf)"

  mkdir -p "$(dirname "$(lud_config_file)")"
  jq -n \
    --arg backup "$(lud_backup_dir)" \
    --arg rclone "$(command -v rclone || true)" \
    --arg rclone_conf "$rclone_conf" \
    --arg remote "$LUD_REMOTE" \
    --argjson games "$games" \
    --argjson ignored "$ignored" \
    --argjson redirects "$redirects" \
    '{
      # No manifest and no release check: gotg says what the save sets are, and
      # a save command must not depend on reaching the internet to describe
      # what is on this disk.
      manifest: {enable: false},
      release: {check: false},
      backup: {
        path: $backup,
        format: {chosen: "simple"},
        # Three, so a pull that turns out to be the wrong choice is recoverable
        # from the tool itself and not only from the archive gotg keeps.
        retention: {full: 3, differential: 0},
        filter: {ignoredPaths: $ignored}
      },
      restore: {path: $backup},
      redirects: $redirects,
      apps: {rclone: {path: $rclone,
                      arguments: ("--fast-list --ignore-checksum" +
                                  (if $rclone_conf == "" then ""
                                   else " --config " + $rclone_conf end))}},
      cloud: {remote: (if $rclone_conf == "" then null
                       else {Custom: {id: $remote}} end),
              path: $remote, synchronize: true},
      customGames: $games
    }' >"$(lud_config_file)"
}

# Ludusavi, pointed at the config above rather than at the user's.
lud_run() {
  XDG_CONFIG_HOME="$(lud_home)" ludusavi --no-manifest-update "$@"
}

# A scan, as JSON. Ludusavi exits non-zero when a game has nothing to scan, and
# for our purposes that is an answer rather than a failure.
lud_scan() {
  local mode="$1" attr="$2" out
  out="$(lud_run "$mode" --preview --api "$attr" 2>/dev/null)" || return 1
  jq -e --arg a "$attr" '.games[$a] // empty' <<<"$out" 2>/dev/null || return 1
}

lud_file_count() { jq -r '.files | length' <<<"$1"; }
lud_byte_count() { jq -r '[.files[].bytes // 0] | add // 0' <<<"$1"; }
lud_changed_count() {
  jq -r '[.files[] | select((.change // "Same") != "Same")] | length' <<<"$1"
}

# Refuse to move something that is obviously not a save, naming what made it
# large. This is gotg's guard, not Ludusavi's: it is what catches a glob that
# has quietly started matching a disc image, and it is worth more than any
# exclude list because it does not have to be complete in order to work.
lud_check_size() {
  local attr="$1" scan="$2" total max
  total="$(lud_byte_count "$scan")"
  max="$(saves_max_bytes)"
  ((total > max)) || return 0

  local biggest
  biggest="$(jq -r '.files | to_entries | sort_by(-(.value.bytes // 0)) | .[:5][]
                    | "\(.value.bytes // 0)\t\(.key)"' <<<"$scan" |
    while IFS=$'\t' read -r size file; do
      printf '       %10s  %s\n' "$(human_size "$size")" "$file"
    done)"
  die "the saves for $attr come to $(human_size "$total"), over the $(human_size "$max") limit.
     Nothing was uploaded. The largest are:
$biggest
     If one of those is not a save, exclude it in client/env; if they are all
     real, raise GOTG_SAVES_MAX_BYTES."
}

# ------------------------------------------------------- receiving from a remote

# Where the backup says these files came from, and whether it is somewhere we
# are willing to write.
#
# A restore writes to the absolute paths recorded in the backup's own
# mapping.yaml, which arrived over the network. Left unchecked that is a
# write-anywhere primitive: a mapping naming /home/you/.bashrc would have
# Ludusavi dutifully restore it. So every recorded path must sit under a root
# that ends in this environment's own name — which is the one thing gotg knows
# must be true of a directory it created — and the redirect that follows moves
# that root, whole, to where this machine keeps it.
lud_recorded_root() {
  local attr="$1" scan="$2" paths=() first root path
  mapfile -t paths < <(jq -r '.files | keys[]' <<<"$scan")
  [[ ${#paths[@]} -gt 0 ]] || return 1

  first="${paths[0]}"
  case "$first" in
    */"$attr"/*) root="${first%%/"$attr"/*}/$attr" ;;
    *) die "$attr: the backup restores to $first, which is outside any $attr directory.
     Refusing to restore it. Nothing local has been touched." ;;
  esac

  for path in "${paths[@]}"; do
    [[ "$path" == "$root"/* ]] ||
      die "$attr: the backup restores to $path, which is outside $root.
     Refusing to restore it. Nothing local has been touched."
    case "/$path/" in
      */../*)
        die "$attr: the backup restores to $path, which climbs outside the directory it names.
     Refusing to restore it. Nothing local has been touched."
        ;;
    esac
  done

  printf '%s' "$root"
}
