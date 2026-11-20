# shellcheck shell=bash
# melonDS bindings: player one's pad, written as JSON for the environment's
# preLaunch to merge into melonDS.toml (the client carries no TOML writer).
#
# melonDS binds raw SDL joystick inputs, not gamepad elements, so each DS button
# is looked up in the pad's map from gotg-pads. One int per input, as
# EmuInstance::joystickButtonDown reads it: a button is its index, a hat is
# 0x100 | hat << 4 | direction, and an axis is 0x10000 | axis << 24 | dir << 20
# (0 positive, 1 negative, 2 trigger) over a low half of 0xFFFF. A button and an
# axis can share one int, which is how the D-pad and the left stick both move.

pads_melonds_file() { printf '%s/melonds-pad.json' "$(env_state_dir "$1")"; }

# DS input -> the SDL elements that drive it, by position: the DS's A is on the
# right and B at the bottom, the same diamond as a SNES pad.
GOTG_MELONDS_BUTTONS='{
  "A": ["b"], "B": ["a"], "X": ["y"], "Y": ["x"],
  "L": ["leftshoulder"], "R": ["rightshoulder"],
  "Select": ["back"], "Start": ["start"],
  "Up": ["dpup", "lefty-"], "Down": ["dpdown", "lefty+"],
  "Left": ["dpleft", "leftx-"], "Right": ["dpright", "leftx+"]
}'

# One pad's bindings, as melonDS.toml keys -> ints.
#
#   $1 the pad's map, as JSON   $2 its SDL joystick index
pads_melonds_bindings() {
  jq -c --argjson map "$1" --argjson index "$2" --argjson want "$GOTG_MELONDS_BUTTONS" '
    def element($e):
      ($e | test("[-+]$")) as $signed
      | ($e | sub("[-+]$"; "")) as $name
      | $map[$name] // empty
      | if .type == "button" then {low: .index}
        elif .type == "hat" then {low: (256 + .index * 16 + .mask)}
        elif .type == "axis" then
          {axis: (65536 + .index * 16777216
                  + (if $signed then (if ($e | endswith("-")) then 1 else 0 end) else 2 end) * 1048576)}
        else empty end;
    def code($elements):
      [ $elements[] as $e | element($e) ] as $parts
      | if ($parts | length) == 0 then empty
        else (([ $parts[] | .low // empty ] | first) // 65535) as $low
          | (([ $parts[] | .axis // empty ] | first) // 0) as $axis
          | $low + $axis
        end;
    { "Instance0.JoystickID": $index }
    + ( $want | to_entries
        | map(. as $b | (code($b.value) // null) as $c
              | select($c != null) | {key: "Instance0.Joystick.\($b.key)", value: $c})
        | from_entries )
  ' <<<'null'
}

# Player one's SDL index inside the game: melonDS opens a joystick by its
# position among those it can see, and under `danstick-rs exec` that is only
# danstick's clones, in the order SDL enumerates them.
#
#   $1 gotg-pads' enumeration   $2 the chosen pad   $3 danstick's published seats
pads_melonds_index() {
  jq -r --argjson pad "$2" --argjson published "${3:-[]}" '
    (if ($published | length) > 0
     then [ .[] | select(. as $d | $published | any(.guid == $d.identity or .name == $d.name)) ]
     else . end)
    | map(.identity == $pad.identity and .slot == $pad.slot) | index(true) // empty
  ' <<<"$1"
}

pads_melonds_configure() {
  local attr="$1" pads published="" seating first index file
  file="$(pads_melonds_file "$attr")"
  pads="$(pads_enumerate)" || return 0
  if declare -F danstick_published >/dev/null; then
    published="$(danstick_published 2>/dev/null || true)"
  fi
  seating="$(pads_seating "$pads" "$published")"
  first="$(jq -c '.[0] // empty' <<<"$seating")"
  # Nobody seated: the last binding stays, so a pad seated mid-game still works
  # on the next launch rather than being unbound by this one.
  [[ -n "$first" ]] || return 0
  index="$(pads_melonds_index "$pads" "$first" "$published")"
  [[ -n "$index" ]] || return 0

  mkdir -p "$(dirname "$file")"
  if ! pads_melonds_bindings "$(jq -c '.map' <<<"$first")" "$index" >"$file.part"; then
    rm -f "$file.part"
    warn "could not write melonDS bindings for $attr"
    return 0
  fi
  mv "$file.part" "$file"
  log "player 1: $(jq -r '.name' <<<"$first") -> melonDS (joystick $index)"
}
