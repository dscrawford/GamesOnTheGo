# shellcheck shell=bash
# Writing PCSX2's controller bindings: the PlayStation 2 half of what pads.sh
# does for ares.
#
# PCSX2 names an SDL input by its standard gamepad element (`SDL-0/FaceSouth`,
# `SDL-0/-LeftY`) and does the per-model lookup itself, so the section is the
# same for every pad and only the device number moves. That number is SDL's
# player index, handed out in the order pads appear; under `danstick-rs exec`
# the game sees only the clones, published in seat order, so player N is SDL-N.

# Two ports and no multitap: what a console on its own has.
GOTG_PCSX2_PORTS=2

# One [PadN] section, a DualShock 2 on SDL device `index`.
pads_pcsx2_section() {
  local port="$1" index="$2" dev
  dev="SDL-$index"
  printf '[Pad%s]\n' "$port"
  printf 'Type = DualShock2\n'
  local pair
  for pair in \
    Up=DPadUp Down=DPadDown Left=DPadLeft Right=DPadRight \
    Cross=FaceSouth Circle=FaceEast Square=FaceWest Triangle=FaceNorth \
    Select=Back Start=Start Analog=Guide \
    L1=LeftShoulder R1=RightShoulder L2=+LeftTrigger R2=+RightTrigger \
    L3=LeftStick R3=RightStick \
    LUp=-LeftY LDown=+LeftY LLeft=-LeftX LRight=+LeftX \
    RUp=-RightY RDown=+RightY RLeft=-RightX RRight=+RightX \
    LargeMotor=LargeMotor SmallMotor=SmallMotor; do
    printf '%s = %s/%s\n' "${pair%%=*}" "$dev" "${pair#*=}"
  done
}

# Replace both [PadN] sections and leave every other one as it was. Both go
# even when one player is seated: a pad unplugged since the last run would
# otherwise keep port two.
pads_pcsx2_rewrite() {
  local file="$1" body="$2"
  local tmp="$file.gotg-tmp"

  mkdir -p "$(dirname "$file")" && touch "$file" || return 1
  awk '
    /^\[/ { drop = ($0 ~ /^\[Pad[1-8]\]$/) }
    !drop { print }
  ' "$file" >"$tmp" || {
    rm -f "$tmp"
    return 1
  }
  printf '%s' "$body" >>"$tmp"
  mv "$tmp" "$file"
}

# Seat each controller in the PS2 port of its own number. Never fatal, as for
# the other emulators: no controller is a launch on the keyboard.
pads_pcsx2_configure() {
  local attr="$1" file pads seating count

  file="$(env_state_dir "$attr")/config/PCSX2/inis/PCSX2.ini"
  pads="$("$(pads_bin)" 2>/dev/null)" || return 0
  seating="$(pads_seating "$pads")"
  count="$(jq 'length' <<<"$seating")"
  ((count > 0)) || return 0
  ((count <= GOTG_PCSX2_PORTS)) || count=$GOTG_PCSX2_PORTS

  local player port name body=""
  for ((player = 0; player < count; player++)); do
    port=$((player + 1))
    name="$(jq -r ".[$player].name" <<<"$seating")"
    body+="$(pads_pcsx2_section "$port" "$player")"$'\n'
    log "player $port: $name -> PS2 port $port"
  done
  # A port nobody sat in says so, or PCSX2 puts its default pad there.
  for ((port = count + 1; port <= GOTG_PCSX2_PORTS; port++)); do
    body+="[Pad$port]"$'\n'"Type = None"$'\n'
  done

  pads_pcsx2_rewrite "$file" "$body" ||
    warn "could not write bindings for $attr"
}
