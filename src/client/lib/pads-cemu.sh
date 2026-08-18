# shellcheck shell=bash
# Cemu motion: one flag in a profile that already exists.
#
# Unlike ares and dolphin, Cemu's bindings are not generated here. Its profile
# is a mapping table keyed by Cemu's own button numbering, and a profile is
# something a person makes once in a settings screen and keeps. What is worth
# writing is the one thing that screen hides: <motion>, which is off in every
# profile made before the pad had a gyro, and which cannot be turned on from
# the command line any other way.
#
# The pad decides. Cemu asks SDL for both sensors —
# SDLController::has_motion() is `m_has_gyro && m_has_accel` — and its
# use_motion() is that AND the profile flag, so setting the flag for a pad
# without sensors would change nothing at all. Setting it for one that has them
# is the difference between a gyro that works and a gyro that is ignored.

pads_cemu_config_dir() {
  local attr="$1" manifest
  manifest="$(env_pads_manifest "$attr")"
  # An isolated environment keeps Cemu's configuration under its own state
  # directory; an unisolated one is the player's own install, and its config is
  # where Cemu would have put it anyway. Read from the derivation rather than
  # guessed from what exists, so a first launch — when neither directory is
  # there yet — does not quietly pick the player's.
  if [[ -f "$manifest" ]] && jq -e '.isolate == true' >/dev/null 2>&1 <"$manifest"; then
    printf '%s/config/Cemu' "$(env_state_dir "$attr")"
  else
    printf '%s/Cemu' "${XDG_CONFIG_HOME:-$HOME/.config}"
  fi
}

# The emulated controller that carries motion to a Wii U game.
#
# Only the GamePad does. The Wii U Pro Controller has no motion hardware, and
# Cemu's ProController accordingly never reads a motion sample — so a Pro
# profile with the flag set is a gyro that goes nowhere, which is worth saying
# out loud rather than leaving to be discovered in-game.
GOTG_CEMU_MOTION_TYPE="Wii U GamePad"

pads_cemu_configure() {
  local attr="$1" dir profiles pads
  dir="$(pads_cemu_config_dir "$attr")"
  profiles="$dir/controllerProfiles"
  [[ -d "$profiles" ]] || return 0

  pads="$("$(pads_bin)" 2>/dev/null)" || return 0

  # Cemu names a pad "<slot>_<SDL GUID>", where slot counts the controllers
  # with that same GUID seen before it — the same count ares calls a slot, and
  # the same one gotg-pads already reports, so the two cannot disagree about
  # which of two identical pads is which.
  local uuids
  uuids="$(jq -c '[.[] | select(.motion) | "\(.slot)_\(.guid)"]' <<<"$pads")" || return 0
  [[ "$uuids" != "[]" ]] || return 0

  local profile
  for profile in "$profiles"/*.xml; do
    [[ -f "$profile" ]] || continue
    pads_cemu_profile_motion "$profile" "$uuids"
  done
}

# Set <motion> on every SDL controller in one profile whose pad has a gyro.
#
# The file is pugixml's own output — tab-indented, one <controller> block per
# bound pad — and is rewritten wholesale by Cemu when the settings screen is
# saved. So this edits the block it is changing and copies every other line
# through, which is what keeps a profile carrying settings this does not know
# about from being reduced to the ones it does.
pads_cemu_profile_motion() {
  local profile="$1" uuids="$2" tmp type status=0

  tmp="$profile.gotg-tmp"
  uuids="$uuids" awk '
    BEGIN {
      # The uuids to act on, as a JSON array of strings — parsed here rather
      # than with jq, because this is the one value crossing the boundary and
      # awk is already reading the file.
      n = split(ENVIRON["uuids"], parts, "\"")
      for (i = 2; i <= n; i += 2) want[parts[i]] = 1
    }

    function field(line, name,   v) {
      if (line !~ "<" name ">") return ""
      v = line
      sub(".*<" name ">", "", v)
      sub("</" name ">.*", "", v)
      return v
    }

    # A block is buffered whole: whether it needs the flag is only known from
    # its uuid, which may appear after the <motion> line it would change.
    /<controller>/ { inBlock = 1; count = 0; uuid = ""; api = ""; motionAt = 0; anchorAt = 0 }

    !inBlock { print; next }

    {
      block[++count] = $0
      if (uuid == "") uuid = field($0, "uuid")
      if (api == "") api = field($0, "api")
      if ($0 ~ /<motion>/) motionAt = count
      # Where a missing flag goes: Cemu writes it after the display name, and
      # keeping its order keeps the diff to the one line that changed.
      if ($0 ~ /<display_name>/ || (anchorAt == 0 && $0 ~ /<uuid>/)) anchorAt = count
    }

    /<\/controller>/ {
      inBlock = 0
      if (api == "SDLController" && (uuid in want)) {
        if (motionAt) {
          line = block[motionAt]
          if (line ~ /<motion>false<\/motion>/) {
            sub(/<motion>false<\/motion>/, "<motion>true</motion>", line)
            block[motionAt] = line
            changed = 1
          }
        } else if (anchorAt) {
          indent = block[anchorAt]
          sub(/[^\t].*$/, "", indent)
          block[anchorAt] = block[anchorAt] "\n" indent "<motion>true</motion>"
          changed = 1
        }
      }
      for (i = 1; i <= count; i++) print block[i]
      next
    }

    # Nothing to change is the common answer, and it is said in the exit
    # status so the file is left untouched rather than replaced by a copy of
    # itself on every launch.
    END { exit changed ? 0 : 1 }
  ' "$profile" >"$tmp" || status=$?

  if ((status != 0)) || [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    return 0
  fi

  mv "$tmp" "$profile"
  log "motion controls enabled in $(basename "$profile")"

  # Said after the flag is set, not instead of setting it: the flag is right
  # either way, and which emulated controller a game wants is the player's
  # choice to change.
  type="$(sed -n 's:.*<type>\(.*\)</type>.*:\1:p' "$profile" | head -1)"
  if [[ -n "$type" && "$type" != "$GOTG_CEMU_MOTION_TYPE" ]]; then
    warn "$(basename "$profile") emulates a $type, which has no motion hardware —"
    warn "a Wii U game reads the gyro from the $GOTG_CEMU_MOTION_TYPE."
  fi
}
