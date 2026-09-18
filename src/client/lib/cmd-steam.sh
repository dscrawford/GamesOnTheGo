# shellcheck shell=bash
# `gotg steam` — putting a game in Steam without the file picker.
#
# `install` writes a launcher and tells you to add it by hand: Games → Add a
# Non-Steam Game → Browse, change the filter to All Files, find the script,
# rename the entry. That is six steps to run one command, and it has to be
# repeated per variant.
#
# Steam keeps its non-Steam games in a binary shortcuts.vdf, so the file itself
# is handled by client/steam/shortcuts.py. What is here is everything around it:
# which game, which launcher, which Steam account, and the one rule that makes
# the difference between working and silently doing nothing —
#
#   STEAM REWRITES THAT FILE WHEN IT EXITS. Anything written while it is running
#   is discarded without a word — which is the whole reason this is a command
#   and not a line in the README.
#
# That rule cannot be worked around, only waited out: Steam holds the shortcut
# list in memory and hands its own copy back on the way out, so an edit made
# underneath it is not racing, it is being overwritten later by design. The one
# route that does work while Steam runs is Steam's own API, which needs the
# client started with CEF debugging on — not something to require of somebody
# who just wants a game in their library.
#
# So a change asked for while Steam is up is *queued* rather than refused or
# quietly thrown away, and applied the moment Steam is gone. The command
# succeeds either way; what differs is when the file is touched.

steam_usage() {
  cat <<'EOF'
usage: gotg steam <command> [args]

  picker                 put GOTG itself in Steam, to pick a game from a sofa
  add <id> [variant]     put it in Steam, writing the launcher if needed
  remove <id> [variant]  take it out again; `remove picker` takes GOTG out
  art <id> [variant]     fetch its artwork again, --force to replace
                         --from <file|url> to choose the picture yourself
  list                   every non-Steam game Steam knows about
  pending                what is waiting for Steam to close, if anything

Steam rewrites its shortcut file when it exits, so a change made while it is
running would be thrown away. Asked for anyway, it is queued and applied the
next time one of these commands runs with Steam closed.

Steam reads that file once, at startup: restart Steam for a change to show up
in your library.
EOF
}

cmd_steam() {
  local verb="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  # Before anything else, because the whole point of the queue is that it
  # empties itself at the first moment it can, rather than waiting to be
  # remembered. Help is exempt: reading the usage is not a reason to write to
  # Steam's file.
  case "$verb" in
    help | --help | -h | "") : ;;
    *) steam_flush ;;
  esac

  case "$verb" in
    picker) steam_picker "$@" ;;
    add) steam_add "$@" ;;
    remove | rm) steam_remove "$@" ;;
    art | artwork) steam_art "$@" ;;
    list | ls) steam_list "$@" ;;
    pending | queue) steam_pending "$@" ;;
    help | --help | -h | "") steam_usage ;;
    *)
      printf 'error: unknown steam command: %s\n\n' "$verb" >&2
      steam_usage >&2
      exit 1
      ;;
  esac
}

# Steam's per-account directory. Flatpak and the native package put it in
# different places, and an account that has never had a non-Steam game has no
# shortcuts.vdf yet — which is a file to create, not an error.
steam_shortcuts_file() {
  # Overridable so the tests can point at a file of their own rather than at a
  # real Steam library.
  if [[ -n "${GOTG_STEAM_SHORTCUTS:-}" ]]; then
    printf '%s' "$GOTG_STEAM_SHORTCUTS"
    return 0
  fi

  local root candidates=(
    "$HOME/.local/share/Steam"
    "$HOME/.steam/steam"
    "$HOME/.var/app/com.valvesoftware.Steam/data/Steam"
  )
  for root in "${candidates[@]}"; do
    [[ -d "$root/userdata" ]] || continue
    local user users=()
    for user in "$root"/userdata/*/; do
      [[ -d "$user" ]] || continue
      [[ "$(basename "$user")" == "0" ]] && continue
      users+=("$user")
    done
    ((${#users[@]} > 0)) || continue
    if ((${#users[@]} > 1)); then
      warn "several Steam accounts here; using $(basename "${users[0]}")"
    fi
    printf '%sconfig/shortcuts.vdf' "${users[0]}"
    return 0
  done
  die "no Steam account found. Looked in ${candidates[*]}"
}

# Is Steam up, and therefore holding the shortcut file hostage?
#
# The question only matters for Steam's own file. Pointed at another one — which
# is what GOTG_STEAM_SHORTCUTS is for — there is nothing for Steam to overwrite,
# so whether it is running stops mattering. GOTG_STEAM_RUNNING answers for both,
# which is how the queue is tested without a Steam to start.
steam_running() {
  case "${GOTG_STEAM_RUNNING:-}" in
    1) return 0 ;;
    0) return 1 ;;
  esac
  [[ -z "${GOTG_STEAM_SHORTCUTS:-}" ]] || return 1
  pgrep -x steam >/dev/null 2>&1
}

steam_pending_file() {
  printf '%s' "${GOTG_STEAM_PENDING_FILE:-$GOTG_STATE_DIR/steam-pending.json}"
}

# Remember a change Steam is currently in no position to accept.
#
# Keyed on the game, the variant *and* the operation, rather than appended
# blindly: asking for the same thing twice is one entry. Keyed on the operation
# too because the first version was not, and `steam art X` behind a queued
# `steam add X` replaced it — so the add never happened and the art then failed
# for a game that was not in Steam. Two different things to do to one entry are
# two entries, in the order they were asked for.
#
# The exception is a remove, which cancels an add or an artwork fetch that has
# not happened yet: changing your mind should not leave the queue adding a game
# in order to take it straight back out.
steam_queue() {
  local op="$1" want="$2" variant="${3:-}" file tmp
  file="$(steam_pending_file)"
  steam_queue_lock
  [[ -f "$file" ]] || printf '[]\n' >"$file"
  tmp="$(mktemp "$file.XXXXXX")"
  if jq --arg op "$op" --arg id "$want" --arg variant "$variant" \
    'def mine: .id == $id and .variant == $variant;
     [ .[] | select((mine | not) or ($op != "remove" and .op != $op)) ]
     + [ { op: $op, id: $id, variant: $variant } ]' \
    "$file" >"$tmp"; then
    mv "$tmp" "$file"
    steam_queue_unlock
  else
    # Loudly, because the whole promise of queueing is that the change is not
    # lost. A pending file that has stopped being JSON — hand-edited, or a
    # machine that went down mid-write — would otherwise take every change from
    # here on while printing the same reassuring paragraph about it waiting.
    rm -f "$tmp"
    steam_queue_unlock
    die "could not record this in $file — it is no longer readable as JSON.
     Look at it, or remove it and ask again: rm $file"
  fi
}

# One writer at a time. The queue is a file rewritten in place and two runs can
# want it at once — the picker adds one game per press, and a person with two
# terminals is not doing anything strange. The same flock the download and
# firmware caches use, on a lock file of its own so that the losing run waits
# rather than writing over the winner.
steam_queue_lock() {
  local file
  file="$(steam_pending_file)"
  mkdir -p "$(dirname "$file")"
  exec 9>"$file.lock"
  flock -w 10 9 || die "another gotg is writing the Steam queue; try again"
}

steam_queue_unlock() { exec 9>&-; }

# Apply everything that was waiting, now that Steam is not.
#
# The queue is cleared before the work rather than after: a change that fails —
# a game since uninstalled, a mod since disabled — has said so once, and a queue
# that retries it forever would say it at the start of every command from here
# on.
#
# Each runs in a subshell so one bad entry does not abort the rest, and each is
# *checked* before it runs. A subshell on the left of `||` has errexit switched
# off for the whole of it, and `set -e` inside does not bring it back — measured,
# not assumed. Without the check, a queued add whose game had gone walked past
# the death of `manifest_find` — which dies inside a command substitution,
# killing only that — and failed again further down, saying something else.
steam_flush() {
  local file rows=() row op id variant
  file="$(steam_pending_file)"
  [[ -s "$file" ]] || return 0
  ! steam_running || return 0

  # Claimed under the lock so that two runs cannot both take the same rows and
  # apply everything twice. Only objects: a file that has stopped being a list
  # of records would otherwise reach the reads below as a bare number and take
  # the whole command down with a jq error.
  steam_queue_lock
  mapfile -t rows < <(
    jq -c '.[]? | select(type == "object" and (.op | type) == "string")' \
      "$file" 2>/dev/null
  )
  if ((${#rows[@]} == 0)); then
    # The file existed and was not empty, so something was in it and is now
    # going in the bin. Said out loud: a change nobody can see disappearing is
    # the failure this whole queue exists to avoid.
    warn "the Steam queue in $file held nothing readable; discarding it"
    rm -f "$file"
    steam_queue_unlock
    return 0
  fi

  # Before the file is dropped, not after: manifest_ensure dies when there is no
  # catalog cached and no network to fetch one, and a queue deleted on the way
  # into that death is three changes the person made and will never see again.
  manifest_ensure
  rm -f "$file"
  steam_queue_unlock

  log "Steam is closed; applying $(steam_count "${#rows[@]}") that waited for it."
  for row in "${rows[@]}"; do
    # One jq for the three fields, not three: the row is a few dozen bytes and
    # the cost is entirely in spawning. Ordered so that no empty field can come
    # first — a tab is IFS whitespace, so `read` would skip a leading empty one
    # and shift every value left, and `op` is the one field that is never empty.
    IFS=$'\t' read -r op id variant < <(
      jq -r '[.op, .id, (.variant // "")] | @tsv' <<<"$row"
    )

    if ! (manifest_find "$id" >/dev/null 2>&1); then
      warn "queued $(printable "$op") of $(printable "$id"): not in the catalog any more, so it was dropped"
      continue
    fi

    # Each of these asks whether Steam is running again, which this function has
    # already answered — and that is worth the pgrep: Steam starting *during* a
    # long flush puts the entries still to come back in the queue rather than
    # writing them into a file that is about to be overwritten.
    case "$op" in
      add) (steam_add "$id" ${variant:+"$variant"}) || warn "queued add of $id failed" ;;
      remove) (steam_remove "$id" ${variant:+"$variant"}) || warn "queued remove of $id failed" ;;
      art) (steam_art "$id" ${variant:+"$variant"}) || warn "queued art for $id failed" ;;
    esac
  done
}

steam_count() { [[ "$1" == 1 ]] && printf '1 change' || printf '%s changes' "$1"; }

# `add` gets this from env_attr, which also insists the file exists — but a
# variant being removed may have had its environment deleted already, and one
# having artwork fetched need not have been built. Both still turn into a
# launcher path, and the queue now writes such a value down and reads it back
# later, so it is checked against the same rule env.sh uses.
steam_variant_ok() {
  local variant="${1:-}"
  [[ -z "$variant" ]] || [[ "$variant" =~ ^[a-z0-9][a-z0-9_-]*$ ]] ||
    die "invalid variant name: $(printable "$variant")"
}

# What a command should do when Steam is up: remember it, say so, and stop.
# Returns 0 when it queued — the caller is done — and 1 when there is nothing in
# the way and the real work should go ahead.
steam_defer() {
  local op="$1" want="$2" variant="${3:-}"
  steam_running || return 1
  steam_queue "$op" "$want" "$variant"
  log "Steam is running, so this is waiting for it."
  log ""
  log "It rewrites its shortcut file when it exits, so writing now would be"
  log "undone the moment you close it. Close Steam and run any gotg steam"
  log "command — the queue is applied first. Then start Steam: it reads that"
  log "file once, at startup, so it has to be restarted to see the change."
  return 0
}

# What is waiting, for somebody who wants to know rather than guess.
steam_pending() {
  local file line
  file="$(steam_pending_file)"
  if [[ ! -s "$file" ]] || [[ "$(jq -r 'length' "$file" 2>/dev/null)" == "0" ]]; then
    log "nothing is waiting for Steam."
    return 0
  fi
  # Through printable, like every other string this did not choose: jq -r
  # decodes \u001b, and a pending file is a file somebody can edit.
  while IFS= read -r line; do
    printable "$line"
  done < <(
    jq -r '.[]? | select(type == "object")
           | "  \(.op)  \(.id)\(if (.variant // "") == "" then "" else " " + .variant end)"' \
      "$file" 2>/dev/null
  )
  log ""
  log "Applied when Steam is closed and any gotg steam command runs."
}

# Invoked through python rather than by its shebang: /usr/bin/env does not
# exist in a nix build sandbox, so `#!/usr/bin/env python3` fails there — which
# is exactly where the tests run. The python on PATH is the package's own, with
# the vdf module in it.
steam_helper() {
  python3 "${GOTG_STEAM_HELPER:-$GOTG_ROOT/steam/shortcuts.py}" "$@"
}

steam_artwork_helper() {
  python3 "${GOTG_STEAM_ARTWORK:-$GOTG_ROOT/steam/artwork.py}" "$@"
}

# Where Steam keeps a non-Steam game's pictures: beside the shortcuts, keyed by
# the appid the shortcut carries.
steam_grid_dir() { printf '%s/grid' "$(dirname "$(steam_shortcuts_file)")"; }

# What the Steam entry is called, which also feeds the artwork search. A
# variant environment may carry its own title in its built manifest —
# "Majora's Mask (rando)" — read off the GC root like everything else on this
# path, never a nix evaluation. Without one, the catalog title.
#
# A mod always ends up named "<game> (<mod>)". Environments write their titles
# that way, and this puts the parentheses on whatever forgot to: a Steam
# library sorts by name, so the whole point is that a game's mods sit next to
# it and say which mod they are. Without the guarantee here that convention
# holds only until the next environment somebody adds.
steam_display_name() {
  local game="$1" variant="${2:-}" name
  name="$(sanitize_title "$(manifest_field "$game" title)")"
  if [[ -z "$variant" ]]; then
    printf '%s' "$name"
    return 0
  fi
  local root title=""
  root="$GOTG_ROOTS_DIR/$(env_attr "$game" "$variant")"
  if [[ -f "$root/share/gotg/saves.json" ]]; then
    title="$(jq -r '.title // empty' "$root/share/gotg/saves.json")"
  fi
  [[ -n "$title" ]] && name="$(sanitize_title "$title")"
  # Already named for this mod — "(rando)" — is left alone, so a title that
  # says it in its own words does not end up saying it twice.
  case "$name" in
    *"($variant)"*) printf '%s' "$name" ;;
    *) printf '%s (%s)' "$name" "$variant" ;;
  esac
}

# The fetched icon's path: grid art is keyed on the unsigned 32-bit appid,
# whichever way the shortcut spelled it. The vdf is another tool's to write
# too, so what came out of it is validated before bash arithmetic sees it.
steam_icon_path() {
  local appid="$1"
  [[ "$appid" =~ ^-?[0-9]+$ ]] || return 0
  ((appid < 0)) && appid=$((appid + 4294967296))
  printf '%s/%s_icon.ico' "$(steam_grid_dir)" "$appid"
}

# Point the shortcut's own icon field at the fetched icon. Grid art Steam
# finds by filename; the list icon it reads only off the shortcut — so a
# downloaded _icon.ico does nothing until this runs. Best-effort throughout:
# the vdf write needs Steam closed, and artwork must never take the shortcut
# with it. set-icon rather than add, because add recomputes appid and AppName
# — a rename made inside Steam, or the random appid of an entry Steam itself
# created, must survive having a picture attached.
steam_attach_icon() {
  local launcher="$1" icon="$2"
  [[ -n "$icon" && -f "$icon" ]] || return 0

  local current
  current="$(steam_helper --file "$(steam_shortcuts_file)" list |
    jq -r --arg e "$launcher" '.[] | select(.exe == $e) | .icon')" || {
    warn "could not read the shortcut list — the icon stays unattached"
    return 0
  }
  [[ "$current" != "$icon" ]] || return 0

  if [[ -z "${GOTG_STEAM_SHORTCUTS:-}" ]] && pgrep -x steam >/dev/null 2>&1; then
    log "the icon is fetched but Steam is running — close it and rerun to attach it"
    return 0
  fi
  steam_helper --file "$(steam_shortcuts_file)" set-icon \
    --exe "$launcher" --icon "$icon" >/dev/null ||
    warn "could not attach the icon to the shortcut"
}

# The SteamGridDB key. Its own file, like the controller order: it is a
# credential, but not the server password, and it is the one part of this that
# cannot be automated — an unauthenticated request to their API is a 401.
steam_api_key() {
  local file="${GOTG_STEAMGRIDDB_KEY_FILE:-$GOTG_CONFIG_DIR/steamgriddb.json}"
  [[ -f "$file" ]] || return 0
  jq -r '.api_key // empty' "$file" 2>/dev/null || true
}

# The cluster proxy, if this machine has been pointed at one.
#
# It holds the real keys and swaps them in, so a client configured this way
# needs no SteamGridDB key of its own — which is the whole reason it exists.
# One token for our own service beats a key for somebody else's on every laptop
# and Steam Deck, and rotating it is one kubectl command rather than a tour of
# the house.
steam_api_file() { printf '%s' "${GOTG_API_FILE:-$GOTG_CONFIG_DIR/api.json}"; }
steam_api_field() {
  local file
  file="$(steam_api_file)"
  [[ -f "$file" ]] || return 1
  jq -re --arg f "$1" '.[$f] // empty' "$file" 2>/dev/null
}

# It holds a bearer token for a service on the public internet, so it is checked
# the same way the server credentials are — a token anyone on the machine can
# read is a token anyone on the machine can spend.
#
# Called here rather than from steam_api_field, which is only ever run inside a
# command substitution: a die in there ends the subshell and nothing else, so
# the refusal was swallowed by the caller and the token read anyway. The check
# has to happen where it can actually stop the command.
steam_check_api_perms() {
  local file
  file="$(steam_api_file)"
  [[ ! -f "$file" ]] || config_check_perms "$file"
}

# Best-effort by design: a shortcut with no picture is a working shortcut.
#
# Two sources. SteamGridDB is better when it has the game, but needs a key;
# libretro-thumbnails needs nothing and is keyed by No-Intro names, so it runs
# whether or not there is a key and fills whatever the first source left. That
# is why a missing key is a note rather than a reason to stop.
steam_fetch_artwork() {
  local name="$1" appid="$2" game="$3"
  shift 3

  # Overridable so the tests can point at a stand-in rather than the real
  # service, the same seam GOTG_STEAM_SHORTCUTS provides for Steam's own file.
  local base=() lr=() key say_key="no"
  [[ -z "${GOTG_LIBRETRO_URL:-}" ]] || lr=(--libretro-url "$GOTG_LIBRETRO_URL")

  steam_check_api_perms

  local proxy="" proxy_token=""
  proxy="$(steam_api_field url)" || proxy=""
  proxy_token="$(steam_api_field token)" || proxy_token=""

  if [[ -n "${GOTG_STEAMGRIDDB_URL:-}" ]]; then
    # An explicit override wins over everything, including the proxy.
    base=(--base-url "$GOTG_STEAMGRIDDB_URL")
    key="$(steam_api_key)"
    [[ -z "$key" ]] || base+=(--api-key "$key")
    [[ -n "$key" ]] || say_key="yes"
  elif [[ -n "$proxy" && -n "$proxy_token" ]]; then
    # The client sends its own token; the proxy swaps in the real key. Nothing
    # about the helper changes — it is the same API under a prefix.
    base=(--base-url "$proxy/steamgriddb" --api-key "$proxy_token")
  else
    key="$(steam_api_key)"
    if [[ -n "$key" ]]; then
      base=(--api-key "$key")
    else
      say_key="yes"
    fi
  fi

  # A mod's own title leads the search; the game it is a mod of stands
  # behind it, and is always what libretro is asked for.
  local fallback=()
  local base_title
  base_title="$(sanitize_title "$(manifest_field "$game" title)")"
  [[ "$name" == "$base_title" ]] || fallback=(--fallback-name "$base_title")

  local result rc=0
  result="$(steam_artwork_helper --grid-dir "$(steam_grid_dir)" \
    --appid "$appid" --name "$name" "${fallback[@]}" \
    --id "$(manifest_field "$game" id)" \
    --platform "$(manifest_field "$game" platform)" \
    --playlists "$GOTG_DATA/libretro-playlists.json" \
    --cache-dir "$GOTG_STATE_DIR/libretro" \
    "${base[@]}" "${lr[@]}" "$@")" || rc=$?

  # A picture named by hand is the one case that is not best-effort: somebody
  # typed a path, so a path that cannot be used is an error rather than a
  # warning to read past.
  local problem
  problem="$(jq -r '.error // empty' <<<"$result" 2>/dev/null || true)"
  [[ -z "$problem" ]] || die "$problem"
  ((rc == 0)) || {
    warn "artwork fetch failed; the shortcut is fine"
    return 0
  }

  local wrote
  wrote="$(jq -r '.wrote // empty' <<<"$result")"
  if [[ -n "$wrote" ]]; then
    log "artwork: $wrote, from $(jq -r '.source' <<<"$result")"
    return 0
  fi

  local got lr_got kept
  got="$(jq -r '.downloaded | length' <<<"$result")"
  lr_got="$(jq -r '.libretro.downloaded | length' <<<"$result")"
  # Counted, because "everything is already there" and "neither source had
  # anything" both download nothing and are not the same news.
  kept="$(jq -r '(.kept | length) + (.libretro.kept | length)' <<<"$result")"

  if ((got == 0 && lr_got == 0 && kept == 0)); then
    warn "no artwork: $(jq -r '[.skipped, .libretro.skipped] | map(select(.)) | join("; ")' <<<"$result")"
  else
    log "artwork: $((got + lr_got)) file(s)$(jq -r 'if .game then " for \(.game)" else "" end' <<<"$result")"
    ((lr_got == 0)) || log "  $lr_got from libretro-thumbnails"
  fi

  # Said once, at the end, and only when it would have made a difference.
  if [[ "$say_key" == "yes" ]]; then
    log ""
    log "No SteamGridDB key. libretro-thumbnails needs none and was used instead;"
    log "for Switch games, which it has none of, either point at the cluster"
    log "proxy, which holds the key for every machine:"
    log "  echo '{\"url\": \"https://gotg.dcraw.net\", \"token\": \"...\"}' > $(steam_api_file)"
    log "or give this machine its own key:"
    log "  https://www.steamgriddb.com/profile/preferences/api"
    log "  echo '{\"api_key\": \"...\"}' > $GOTG_CONFIG_DIR/steamgriddb.json"
    log "  gotg steam art <id> --force"
  fi
}

steam_art() {
  local want="${1:-}" variant="" opts=()
  [[ -n "$want" ]] || die "usage: gotg steam art <id> [variant] [--force] [--from <file|url> [--as tile|capsule|hero|logo|icon]]"
  shift
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi
  # Passed through rather than enumerated: --from and --as belong to the
  # helper, which is where their meaning and their error messages live.
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force)
        opts+=(--force)
        shift
        ;;
      --from | --as)
        [[ -n "${2:-}" ]] || die "$1 needs a value"
        opts+=("$1" "$2")
        shift 2
        ;;
      *) die "unknown option for steam art: $1" ;;
    esac
  done

  manifest_ensure
  steam_variant_ok "$variant"

  local game launcher name appid
  game="$(manifest_find "$want")"

  # Queued after the game is resolved, so a name that is not one is answered
  # here rather than minutes later — and never when a picture was named, since
  # the queue remembers the game and the variant and nothing else. Coming back
  # to fetch whatever the internet offers, when somebody asked for the file in
  # their pictures folder, is worse than saying no.
  if steam_running && ((${#opts[@]} > 0)); then
    die "artwork chosen by hand cannot wait for Steam: it would come back as
     whatever the search finds. Close Steam and run this again."
  fi
  ! steam_defer art "$want" "$variant" || return 0

  launcher="$(launcher_path "$game" "$variant")"
  name="$(steam_display_name "$game" "$variant")"

  # The id Steam files artwork under is the one on the shortcut, so it is read
  # back rather than recomputed — if the two ever disagree, the shortcut wins.
  appid="$(steam_helper --file "$(steam_shortcuts_file)" list |
    jq -r --arg e "$launcher" '.[] | select(.exe == $e) | .appid')"
  [[ -n "$appid" ]] || die "$name is not in Steam yet — run: gotg steam add $want${variant:+ $variant}"

  steam_fetch_artwork "$name" "$appid" "$game" "${opts[@]}"
  steam_attach_icon "$launcher" "$(steam_icon_path "$appid")"
}

# The picker, as a Steam entry, so Game Mode can reach it.
#
# A launcher script rather than the binary itself: on a Deck the picker comes
# from a nix profile, and the store path changes on every upgrade. Steam
# remembers the path it was given -- it is half of how an entry is identified --
# so a shortcut pointing into the store would stop working at the next
# `gotg sync`, in Game Mode, where there is nothing to read the error.
steam_picker() {
  local name="${GOTG_PICKER_NAME:-Games On The Go}"
  local launcher="$GOTG_STATE_DIR/launchers/gotg-ui.sh"

  # Overridable like every other tool this shells out to: a test needs to be
  # able to say there is no picker on a machine that has one.
  local picker="${GOTG_PICKER:-}"
  # The root sync keeps current, before whatever PATH has: PATH is the
  # shell's, and the shell that started Steam may be a day older than the
  # one typing this.
  if [[ -z "$picker" && -x "$GOTG_UI_ROOT/bin/gotg-ui" ]]; then
    picker="$GOTG_UI_ROOT/bin/gotg-ui"
  fi
  if [[ -z "$picker" ]]; then
    picker="$(command -v gotg-ui 2>/dev/null)" || picker=""
  fi
  [[ -x "$picker" ]] ||
    die "no gotg-ui here. Install the picker first: nix profile add $GOTG_REMOTE_FLAKE#gotg-ui"

  # The launcher is written whether Steam is open or not. It is our file,
  # read fresh each time the entry is pressed, and only the shortcut file
  # has to wait for Steam to close -- on a Deck, where Steam is up from boot
  # to shutdown, deferring the launcher too meant it was never rewritten and
  # every fix to it sat in the queue.
  steam_write_picker_launcher "$launcher"

  ! steam_defer picker "" "" || return 0

  local result
  result="$(steam_helper --file "$(steam_shortcuts_file)" add \
    --name "$name" --exe "$launcher" --start-dir "$(dirname "$launcher")" \
    --tag "GOTG")" ||
    die "could not write the Steam shortcut"

  log "$(jq -r '"\(.action): \(.name)"' <<<"$result")"
  log "  $launcher -> $picker"
  log ""
  log "Restart Steam and it will be in your library — it reads its shortcut"
  log "file once, at startup, so a Steam that is already open will not see it."
}

# What `gotg steam picker` writes for Steam to press. Its own function so the
# launcher can be refreshed while the shortcut waits.
steam_write_picker_launcher() {
  local launcher="$1"
  mkdir -p "$(dirname "$launcher")"
  # By name, not by path, for the reason above -- resolved against PATH each
  # time it is pressed.
  # Steam's environment carries none of Nix's profile directories, so the
  # picker is looked for rather than assumed on PATH. `exec gotg-ui` exited
  # 127 under Steam before anything was drawn -- which from Game Mode is an
  # entry that opens and closes with nothing to read anywhere. Hence both
  # halves of this: the search, and a log for whatever it has to say.
  cat >"$launcher" <<'LAUNCHER'
#!/usr/bin/env bash
# Written by gotg steam picker. Safe to delete; it is rewritten.
#
# Steam's environment is not a shell's. It carries none of Nix's profile
# directories, and little enough of a PATH that `mkdir` and `date` are not
# to be counted on either -- the first draft of this died on `mkdir: command
# not found` while setting up the log it would have written the reason to.
# So: the timestamp comes from bash itself, and the log is best effort.
# Nothing here may stand between Steam and the picker starting.
gotg_logs="${XDG_STATE_HOME:-$HOME/.local/state}/gotg/logs"
if mkdir -p "$gotg_logs" 2>/dev/null; then
  exec >>"$gotg_logs/gotg-ui.log" 2>&1
fi
printf '%(%FT%T)T: launched by Steam\n' -1

# The whole screen: this entry is pressed from Game Mode, where a window in
# the corner of one is not what anybody meant.
export GOTG_UI_FULLSCREEN=1

# Steam preloads its overlay into everything it starts, and a nix-wrapped
# program cannot carry it: on a Deck the picker died before drawing anything
# with "libGL.so.1: cannot open shared object file". The overlay is the only
# thing lost by dropping that one entry. POSIX sh, no arrays: this runs
# before anything of ours is on PATH.
case "${LD_PRELOAD:-}" in
  *gameoverlayrenderer*)
    kept=""
    for entry in $(printf '%s' "$LD_PRELOAD" | tr ':' ' '); do
      case "$entry" in *gameoverlayrenderer*) ;; *) kept="${kept:+$kept:}$entry" ;; esac
    done
    if [ -n "$kept" ]; then export LD_PRELOAD="$kept"; else unset LD_PRELOAD; fi
    ;;
esac
LAUNCHER

  # A checkout, when this was added from one. The picker on such a machine is
  # a wrapper that finds its source by asking git about the *current*
  # directory -- and Steam starts this script in the launchers directory, so
  # that question has no answer and the wrapper exits before drawing
  # anything. It only appeared to work because Steam, started from a terminal
  # in the checkout, had inherited the variable.
  #
  # A default rather than an override: a machine that sets its own still wins,
  # and a packaged picker ignores this entirely.
  local dev_root=""
  for dev_root in "${GOTG_DEV_ROOT:-}" "$(git rev-parse --show-toplevel 2>/dev/null || true)"; do
    [[ -n "$dev_root" && -d "$dev_root/src/ui/gotg_ui" ]] && break
    dev_root=""
  done
  if [[ -n "$dev_root" ]]; then
    {
      printf '\n# The checkout this entry was added from; see gotg steam picker.\n'
      printf '%s\n' ": \"\${GOTG_DEV_ROOT:=$dev_root}\""
      printf 'export GOTG_DEV_ROOT\n'
    } >>"$launcher"
  fi

  # Where sync leaves the picker, baked in as a path under the state
  # directory: the symlink moves with every sync, the path to it does not.
  {
    printf '\n# The root gotg sync keeps current; see gotg steam picker.\n'
    printf 'gotg_ui_root=%s\n' "$(printf '%q' "$GOTG_UI_ROOT")"
  } >>"$launcher"

  cat >>"$launcher" <<'LAUNCHER'

# The synced root first: the build `gotg sync` last made, whatever PATH says.
# Then by name, so a machine without a synced root still follows an upgrade;
# then the places a Nix profile puts it, because Steam's PATH has none.
for gotg_ui in \
  "$gotg_ui_root/bin/gotg-ui" \
  "$(command -v gotg-ui 2>/dev/null)" \
  "$HOME/.nix-profile/bin/gotg-ui" \
  "$HOME/.local/state/nix/profile/bin/gotg-ui" \
  "/nix/var/nix/profiles/default/bin/gotg-ui"; do
  [ -n "$gotg_ui" ] && [ -x "$gotg_ui" ] && exec "$gotg_ui" "$@"
done

printf '%(%FT%T)T: no gotg-ui on PATH (%s) or in any Nix profile.\n' -1 "$PATH"
printf 'Install it with: nix profile add github:dscrawford/GamesOnTheGo#gotg-ui\n'
exit 127
LAUNCHER
  chmod +x "$launcher"
}

steam_add() {
  local want="${1:-}" variant=""
  [[ -n "$want" ]] || die "usage: gotg steam add <id> [variant]"
  shift
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  manifest_ensure

  local game launcher
  game="$(manifest_find "$want")"
  # env_attr validates the variant, so a typo is caught here rather than
  # becoming a Steam entry that fails at launch.
  local attr
  attr="$(env_attr "$game" "$variant")"
  # And neither does a mod no version here can run. It would add cleanly, take
  # its place in the library with artwork, and refuse the moment it is pressed
  # — from inside Steam, where the sentence explaining why has nowhere to go.
  versions_variant_runnable "$game" "$attr" ||
    die "$(steam_display_name "$game" "$variant") $(versions_variant_why "$attr"),
     and that is not what is installed. See: gotg versions $(manifest_field "$game" id)"

  # Everything above is worth doing while Steam is up: a typo, a game that is
  # not here, a mod no version suits are all answered now rather than saved up
  # and reported minutes later, out of context.
  ! steam_defer add "$want" "$variant" || return 0

  launcher="$(launcher_path "$game" "$variant")"
  [[ -f "$launcher" ]] || launcher="$(launcher_write "$game" "$variant")"

  local name
  name="$(steam_display_name "$game" "$variant")"

  local result
  result="$(steam_helper --file "$(steam_shortcuts_file)" add \
    --name "$name" --exe "$launcher" --start-dir "$(dirname "$launcher")" \
    --tag "$(manifest_field "$game" platform)")" ||
    die "could not write the Steam shortcut"

  log "$(jq -r '"\(.action): \(.name)"' <<<"$result")"
  log "  $launcher"

  local new_appid
  new_appid="$(jq -r '.appid' <<<"$result")"
  steam_fetch_artwork "$name" "$new_appid" "$game"
  steam_attach_icon "$launcher" "$(steam_icon_path "$new_appid")"

  log ""
  log "Restart Steam and it will be in your library — it reads its shortcut"
  log "file once, at startup, so a Steam that is already open will not see it."
}

steam_remove() {
  local want="${1:-}" variant=""
  [[ -n "$want" ]] || die "usage: gotg steam remove <id> [variant]"
  shift
  if [[ $# -gt 0 && "$1" != -* ]]; then
    variant="$1"
    shift
  fi

  local game launcher result
  if [[ "$want" == picker ]]; then
    # The picker's own entry, which `gotg steam picker` wrote and no game id
    # names. What uninstall.sh asks for.
    launcher="$GOTG_STATE_DIR/launchers/gotg-ui.sh"
  else
    manifest_ensure
    steam_variant_ok "$variant"
    game="$(manifest_find "$want")"
    launcher="$(launcher_path "$game" "$variant")"
  fi

  ! steam_defer remove "$want" "$variant" || return 0

  result="$(steam_helper --file "$(steam_shortcuts_file)" remove --exe "$launcher")" ||
    die "could not rewrite the Steam shortcuts"

  case "$(jq -r '.action' <<<"$result")" in
    removed) log "removed: $(jq -r '.name' <<<"$result")" ;;
    *) log "not in Steam: $launcher" ;;
  esac
}

steam_list() {
  local file result
  file="$(steam_shortcuts_file)"
  result="$(steam_helper --file "$file" list)" || die "could not read $file"

  if [[ "$(jq 'length' <<<"$result")" == "0" ]]; then
    log "no non-Steam games."
    return 0
  fi
  jq -r --arg h "$C_HEAD" --arg m "$C_MUTED" --arg r "$C_RESET" \
    '.[] | "\($h)\(.name)\($r)\n  \($m)\(.exe)\($r)"' <<<"$result" >&2
}
