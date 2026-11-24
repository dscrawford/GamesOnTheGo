# shellcheck shell=bash
# melonDS bindings for player one, as JSON the environment's preLaunch merges
# into melonDS.toml. Ints as EmuInstance::joystickButtonDown decodes them:
# button = index, hat = 0x100|hat<<4|dir, axis = 0x10000|axis<<24|dir<<20
# (0 +, 1 -, 2 trigger); one int can hold a button and an axis at once.

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

# melonDS opens a joystick by SDL index, and under `danstick-rs exec` the game
# sees only danstick's clones.
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
  # Nobody seated: keep the last binding rather than unbinding it.
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
