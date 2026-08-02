# shellcheck shell=bash
# Writing Dolphin's controller bindings — the GameCube and Wii half of what
# pads.sh does for ares.
#
# Much less work than ares, for one reason: Dolphin's SDL backend names inputs
# by their standard gamepad element — `Button S`, `Left Y+`, `Pad N` — and does
# the per-model lookup itself. So there is no table mapping a console button to
# a raw index, and no need to ask SDL what this pad calls its A button. The
# strings below are copied from a GCPadNew.ini Dolphin wrote for a real pad,
# the same ground-truth rule the ares side follows.
#
# What does have to be right is the device line. Dolphin spells it
# SDL/<n>/<name>, where n counts the devices already sharing that name, and the
# whole binding is silently inert if it names a device Dolphin cannot see. Our
# slot counts devices sharing a GUID rather than a name, which agrees wherever
# same-named pads are the same model — two identical pads share a GUID and are
# numbered 0 and 1 by both.

# Dolphin's own name for a standard controller in the SI port table.
GOTG_DOLPHIN_SI_GAMECUBE=6

# One [GCPadN] section.
#
# The main stick answers to the D-pad as well, so the two are interchangeable
# the way they are on the ares consoles: `|` is Dolphin's or. It does mean a
# game reading both gets both, which is the price of the D-pad working in a
# game that only ever reads the stick.
pads_dolphin_section() {
  local port="$1" device="$2"
  printf '[GCPad%s]\n' "$port"
  printf 'Device = %s\n' "$device"
  cat <<'EOF'
Buttons/A = `Button S`
Buttons/B = `Button E`
Buttons/X = `Button N`
Buttons/Y = `Button W`
Buttons/Z = `Shoulder L`
Buttons/Start = `Start`
Main Stick/Up = `Left Y+`|`Pad N`
Main Stick/Down = `Left Y-`|`Pad S`
Main Stick/Left = `Left X-`|`Pad W`
Main Stick/Right = `Left X+`|`Pad E`
Main Stick/Calibration = 100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42
C-Stick/Up = `Right Y+`
C-Stick/Down = `Right Y-`
C-Stick/Left = `Right X-`
C-Stick/Right = `Right X+`
C-Stick/Calibration = 100.00 141.42 100.00 141.42 100.00 141.42 100.00 141.42
Triggers/L = `Trigger L`
Triggers/R = `Trigger R`
Triggers/L-Analog = `Trigger L`
Triggers/R-Analog = `Trigger R`
D-Pad/Up = `Pad N`
D-Pad/Down = `Pad S`
D-Pad/Left = `Pad W`
D-Pad/Right = `Pad E`
EOF
}

# Replace every [GCPad1..4] section with what was generated, and leave the rest
# of the file — other sections, other devices — exactly as it was.
#
# All four go, not just the ones being written: a controller that has been
# unplugged since the last run would otherwise keep its port.
pads_dolphin_rewrite() {
  local file="$1" body="$2"
  local tmp="$file.gotg-tmp"

  touch "$file"
  awk '
    /^\[/ { drop = ($0 ~ /^\[GCPad[1-4]\]$/) }
    !drop { print }
  ' "$file" >"$tmp" || {
    rm -f "$tmp"
    return 1
  }
  printf '%s' "$body" >>"$tmp"
  mv "$tmp" "$file"
}

# Set one key of one section of an ini, adding either if it is missing.
pads_dolphin_ini_set() {
  local file="$1" section="$2" key="$3" value="$4"
  local tmp="$file.gotg-tmp"

  touch "$file"
  awk -v section="[$section]" -v key="$key" -v value="$value" '
    BEGIN { line = key " = " value }
    # Leaving the section without having written the key: write it now, before
    # the header that ends the section.
    /^\[/ {
      if (inSection && !written) { print line; written = 1 }
      inSection = ($0 == section)
    }
    inSection && index($0, key " ") == 1 { print line; written = 1; next }
    { print }
    END {
      if (!written) {
        if (!inSection) print section
        print line
      }
    }
  ' "$file" >"$tmp" || {
    rm -f "$tmp"
    return 1
  }
  mv "$tmp" "$file"
}

# Seat each attached controller in the GameCube port of its own number.
#
# Never fatal, for the same reason the ares side is not: a launch with no
# controller is a launch on the keyboard, which is worse than a bound pad and
# very much better than not starting.
pads_dolphin_configure() {
  local attr="$1"
  local state config pads seating count

  state="$(env_state_dir "$attr")"
  config="$state/config/dolphin-emu"
  # Dolphin writes these itself on first run, but bindings are worth having on
  # the first run too — it reads the file at startup either way.
  mkdir -p "$config" || return 0

  pads="$("$(pads_bin)" 2>/dev/null)" || return 0
  seating="$(pads_seating "$pads")"
  count="$(jq 'length' <<<"$seating")"
  ((count > 0)) || return 0
  ((count <= GOTG_MAX_PLAYERS)) || count=$GOTG_MAX_PLAYERS

  local player port slot name body=""
  for ((player = 0; player < count; player++)); do
    port=$((player + 1))
    slot="$(jq -r ".[$player].slot" <<<"$seating")"
    name="$(jq -r ".[$player].name" <<<"$seating")"

    body+="$(pads_dolphin_section "$port" "SDL/$slot/$name")"$'\n'

    # A port with no controller declared in it is ignored however well its pad
    # is bound, so this is as load-bearing as the bindings themselves.
    pads_dolphin_ini_set "$config/Dolphin.ini" Core \
      "SIDevice$player" "$GOTG_DOLPHIN_SI_GAMECUBE" ||
      warn "could not declare a controller in port $port for $attr"

    log "player $port: $name -> GameCube port $port"
  done

  pads_dolphin_rewrite "$config/GCPadNew.ini" "$body" ||
    warn "could not write bindings for $attr"
}
