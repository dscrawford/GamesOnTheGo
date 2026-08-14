#!/usr/bin/env bats
# Generating ares controller bindings.
#
# The expected strings here are not invented: they are what ares itself wrote
# after binding a Steam Controller by hand, so these tests are a comparison
# against ground truth rather than against my reading of the format.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env
  load_client_libs
  ID=03002854de2800000413000002006800
  # As gotg-pads reports it: SDL's own mapping for the pad in question.
  MAP='{"a":{"type":"button","index":0},
        "b":{"type":"button","index":1},
        "back":{"type":"button","index":4},
        "start":{"type":"button","index":6},
        "dpup":{"type":"hat","index":0,"mask":1},
        "dpdown":{"type":"hat","index":0,"mask":4},
        "dpleft":{"type":"hat","index":0,"mask":8},
        "dpright":{"type":"hat","index":0,"mask":2},
        "lefttrigger":{"type":"axis","index":2,"min":-32768,"max":32767},
        "leftx":{"type":"axis","index":0,"min":-32768,"max":32767},
        "lefty":{"type":"axis","index":1,"min":-32768,"max":32767}}'
}

@test "a button becomes group 3 and its raw index" {
  run pads_ares_assignment "$ID" 0 "$MAP" a
  [ "$output" = "$ID/0/3/0" ]
  run pads_ares_assignment "$ID" 0 "$MAP" start
  [ "$output" = "$ID/0/3/6" ]
}

@test "a hat becomes a pair of pseudo-axes, exactly as ares wrote them" {
  # ares splits hat N into inputs 2N and 2N+1 — X then Y — with Lo for up and
  # left. These four strings are copied from a settings.bml ares produced.
  run pads_ares_assignment "$ID" 0 "$MAP" dpup
  [ "$output" = "$ID/0/1/1/Lo" ]
  run pads_ares_assignment "$ID" 0 "$MAP" dpdown
  [ "$output" = "$ID/0/1/1/Hi" ]
  run pads_ares_assignment "$ID" 0 "$MAP" dpleft
  [ "$output" = "$ID/0/1/0/Lo" ]
  run pads_ares_assignment "$ID" 0 "$MAP" dpright
  [ "$output" = "$ID/0/1/0/Hi" ]
}

@test "a second identical pad differs only by slot" {
  run pads_ares_assignment "$ID" 1 "$MAP" a
  [ "$output" = "$ID/1/3/0" ]
}

@test "an element this controller lacks binds nothing rather than guessing" {
  run pads_ares_assignment "$ID" 0 "$MAP" rightshoulder
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "the rewrite touches one console's Gamepad block and nothing else" {
  # Sibling blocks carry the same button names, and a whole-file rewrite would
  # take ares' other settings with it.
  cat >"$TEST_TMP/settings.bml" <<'BML'
Video
  Driver: OpenGL
SuperFamicom
  Input
    Controller.Port.1
      Gamepad
        Up: 0x1/0/57;;
        B: 0x1/0/95;;
      Rumble.Gamepad
        Up: ;;
        B: ;;
    Controller.Port.2
      Gamepad
        Up: ;;
        B: ;;
Nintendo64
  Input
    Controller.Port.1
      Gamepad
        Up: ;;
        B: ;;
BML

  pads_ares_rewrite "$TEST_TMP/settings.bml" SuperFamicom Controller.Port.1 Gamepad \
    "$(jq -nc '{Up: "GUID/0/1/1/Lo;;", B: "GUID/0/3/0;;"}')"

  run cat "$TEST_TMP/settings.bml"
  [ "$status" -eq 0 ]

  # Port 1's Gamepad got both bindings.
  [[ "$output" == *"        Up: GUID/0/1/1/Lo;;"* ]]
  [[ "$output" == *"        B: GUID/0/3/0;;"* ]]
  # Exactly one line each — the siblings, the second port and the other console
  # are all still empty.
  run grep -c "GUID" "$TEST_TMP/settings.bml"
  [ "$output" = "2" ]
  # And an unrelated setting is untouched.
  grep -q "Driver: OpenGL" "$TEST_TMP/settings.bml"
}

@test "a console the table does not cover is left alone" {
  cat >"$TEST_TMP/settings.bml" <<'BML'
MegaDrive
  Input
    Controller.Port.1
      Gamepad
        Up: ;;
BML
  pads_ares_rewrite "$TEST_TMP/settings.bml" SuperFamicom Controller.Port.1 Gamepad \
    "$(jq -nc '{Up: "GUID/0/1/1/Lo;;"}')"
  run grep -c "GUID" "$TEST_TMP/settings.bml"
  [ "$output" = "0" ]
}

@test "an axis binds one direction at a time, and the sign says which" {
  # "lefty" is a whole stick; only "lefty-" is up. Without the sign there is
  # nothing in the name to decide it.
  run pads_ares_assignment "$ID" 0 "$MAP" lefty-
  [ "$output" = "$ID/0/0/1/Lo" ]
  run pads_ares_assignment "$ID" 0 "$MAP" lefty+
  [ "$output" = "$ID/0/0/1/Hi" ]
  run pads_ares_assignment "$ID" 0 "$MAP" leftx-
  [ "$output" = "$ID/0/0/0/Lo" ]
}

@test "a trigger needs no sign — it rests at one end and travels one way" {
  run pads_ares_assignment "$ID" 0 "$MAP" lefttrigger
  [ "$output" = "$ID/0/0/2/Hi" ]
}

@test "the stick doubles as the D-pad on a console that never had one" {
  # Both are bound to the same input, in two of ares' three slots, so either
  # drives it. This reads the shipped table rather than a fixture: the point is
  # that SNES really is set up this way.
  run pads_ares_bindings SuperFamicom "$ID" 0 "$MAP"
  [ "$status" -eq 0 ]
  [ "$(jq -r '.Up' <<<"$output")" = "$ID/0/1/1/Lo;$ID/0/0/1/Lo;" ]
  [ "$(jq -r '.Left' <<<"$output")" = "$ID/0/1/0/Lo;$ID/0/0/0/Lo;" ]
  # A plain button still fills one slot and leaves the other two empty.
  [ "$(jq -r '.B' <<<"$output")" = "$ID/0/3/0;;" ]
}

@test "an input whose elements this pad all lack is left as ares had it" {
  run pads_ares_bindings SuperFamicom "$ID" 0 "$MAP"
  # This pad has no right shoulder, so R is absent rather than empty.
  [ "$(jq 'has("R")' <<<"$output")" = "false" ]
}

@test "a true analog axis is reached one level deeper" {
  cat >"$TEST_TMP/settings.bml" <<'BML'
Nintendo64
  Input
    Controller.Port.1
      Gamepad
        Up: ;;
        X-Axis
          Lo: ;;
          Hi: ;;
        Y-Axis
          Lo: ;;
          Hi: ;;
BML

  pads_ares_rewrite "$TEST_TMP/settings.bml" Nintendo64 Controller.Port.1 Gamepad \
    "$(jq -nc '{"X-Axis/Lo": "GUID/0/0/0/Lo;;", "Y-Axis/Hi": "GUID/0/0/1/Hi;;"}')"

  run cat "$TEST_TMP/settings.bml"
  # The nested lines keep their own indentation and their short names.
  [[ "$output" == *"          Lo: GUID/0/0/0/Lo;;"* ]]
  [[ "$output" == *"          Hi: GUID/0/0/1/Hi;;"* ]]
  # X-Axis/Hi and Y-Axis/Lo were not asked for, so they are still empty: the
  # two blocks are told apart rather than treated as one.
  run grep -c "GUID" "$TEST_TMP/settings.bml"
  [ "$output" = "2" ]
}

seating_fixture() {
  cat <<'JSON'
[{"name":"Steam Controller","identity":"AAA","slot":0,"gamepad":true,"map":{"a":{"type":"button","index":0}}},
 {"name":"Xbox 360 Controller","identity":"BBB","slot":0,"gamepad":true,"map":{"a":{"type":"button","index":0}}},
 {"name":"Keyboard","identity":"CCC","slot":0,"gamepad":false,"map":null}]
JSON
}

pin_order() {
  mkdir -p "$GOTG_CONFIG_DIR"
  jq -n --argjson o "$1" '{order: $o}' >"$GOTG_CONFIG_DIR/controllers.json"
}

@test "with nothing pinned, seating follows SDL and drops what cannot be bound" {
  run pads_seating "$(seating_fixture)"
  [ "$(jq -r 'length' <<<"$output")" = "2" ]
  [ "$(jq -r '.[0].identity' <<<"$output")" = "AAA" ]
  [ "$(jq -r '.[1].identity' <<<"$output")" = "BBB" ]
}

@test "naming one controller is enough — it leads and the rest follow behind" {
  pin_order '["BBB/0"]'
  run pads_seating "$(seating_fixture)"
  [ "$(jq -r '.[0].identity' <<<"$output")" = "BBB" ]
  [ "$(jq -r '.[1].identity' <<<"$output")" = "AAA" ]
  # Still whole records, not just the keys they were matched on.
  [ "$(jq -r '.[0].name' <<<"$output")" = "Xbox 360 Controller" ]
}

@test "a pinned order is followed exactly" {
  pin_order '["BBB/0","AAA/0"]'
  run pads_seating "$(seating_fixture)"
  [ "$(jq -r '[.[].identity] | join(",")' <<<"$output")" = "BBB,AAA" ]
}

@test "a pinned controller that is not attached leaves no gap" {
  # Pinning something that has since been unplugged must not seat a hole where
  # it was — the controllers behind it move up.
  pin_order '["GONE/0","BBB/0"]'
  run pads_seating "$(seating_fixture)"
  [ "$(jq -r 'length' <<<"$output")" = "2" ]
  [ "$(jq -r '.[0].identity' <<<"$output")" = "BBB" ]
}

@test "an unparseable order file falls back to SDL rather than refusing to launch" {
  mkdir -p "$GOTG_CONFIG_DIR"
  printf 'this is not json' >"$GOTG_CONFIG_DIR/controllers.json"
  run pads_seating "$(seating_fixture)"
  [ "$status" -eq 0 ]
  [ "$(jq -r '.[0].identity' <<<"$output")" = "AAA" ]
}

@test "a handheld's pad has no port number" {
  cat >"$TEST_TMP/settings.bml" <<'BML'
GameBoy
  Input
    Game.Boy
      Controls
        Up: ;;
        A: ;;
BML

  pads_ares_rewrite "$TEST_TMP/settings.bml" GameBoy Game.Boy Controls \
    "$(jq -nc '{Up: "GUID/0/1/1/Lo;;"}')"
  run grep -c "GUID" "$TEST_TMP/settings.bml"
  [ "$output" = "1" ]
}

# --- the first launch of a new console ---------------------------------------

@test "a virgin environment gets its section created and bound in one pass" {
  # No settings.bml at all: ares has never run here. The first launch must
  # still produce a working pad, which was the failure that arrived with two
  # whole libraries at once.
  local file="$TEST_TMP/fresh/settings.bml"
  local bindings='{"Up": "ID/0/1/1/Lo;;", "Down": "ID/0/1/1/Hi;;", "A": "ID/0/3/0;;"}'

  pads_ares_ensure_block "$file" GameBoy Game.Boy Controls "$bindings"
  pads_ares_rewrite "$file" GameBoy Game.Boy Controls "$bindings"

  run cat "$file"
  [[ "$output" == *"GameBoy"* ]]
  [[ "$output" == *"        Up: ID/0/1/1/Lo;;"* ]]
  [[ "$output" == *"        A: ID/0/3/0;;"* ]]
}

@test "a missing console section is appended without touching its neighbours" {
  local file="$TEST_TMP/settings.bml"
  cat >"$file" <<'BML'
Video
  Driver: OpenGL
SuperFamicom
  Input
    Controller.Port.1
      Gamepad
        Up: KEEP;;
BML
  local bindings='{"Up": "NEW;;"}'
  pads_ares_ensure_block "$file" Famicom Controller.Port.1 Gamepad "$bindings"
  pads_ares_rewrite "$file" Famicom Controller.Port.1 Gamepad "$bindings"

  grep -q "Driver: OpenGL" "$file"
  grep -q "Up: KEEP;;" "$file"
  # The new section carries the new value; the old one kept its own.
  run awk '/^Famicom$/,0' "$file"
  [[ "$output" == *"Up: NEW;;"* ]]
  [[ "$output" != *"KEEP"* ]]
}

@test "a seeded section nests analog axes the way ares does" {
  local file="$TEST_TMP/n64/settings.bml"
  local bindings='{"A": "ID/0/3/1;;", "X-Axis/Lo": "ID/0/0/0/Lo;;", "X-Axis/Hi": "ID/0/0/0/Hi;;"}'

  pads_ares_ensure_block "$file" Nintendo64 Controller.Port.1 Gamepad "$bindings"
  pads_ares_rewrite "$file" Nintendo64 Controller.Port.1 Gamepad "$bindings"

  run cat "$file"
  [[ "$output" == *"        X-Axis"* ]]
  [[ "$output" == *"          Lo: ID/0/0/0/Lo;;"* ]]
  [[ "$output" == *"          Hi: ID/0/0/0/Hi;;"* ]]
  [[ "$output" == *"        A: ID/0/3/1;;"* ]]
}

@test "a second port joins an existing section rather than duplicating it" {
  local file="$TEST_TMP/two/settings.bml"
  local p1='{"Up": "PAD1;;"}' p2='{"Up": "PAD2;;"}'

  pads_ares_ensure_block "$file" SuperFamicom Controller.Port.1 Gamepad "$p1"
  pads_ares_rewrite "$file" SuperFamicom Controller.Port.1 Gamepad "$p1"
  pads_ares_ensure_block "$file" SuperFamicom Controller.Port.2 Gamepad "$p2"
  pads_ares_rewrite "$file" SuperFamicom Controller.Port.2 Gamepad "$p2"

  run grep -cx "SuperFamicom" "$file"
  [ "$output" = "1" ]
  run grep -cx "  Input" "$file"
  [ "$output" = "1" ]
  grep -q "Up: PAD1;;" "$file"
  grep -q "Up: PAD2;;" "$file"
}

@test "ensuring an already complete block changes nothing" {
  local file="$TEST_TMP/idem/settings.bml"
  local bindings='{"Up": "V;;"}'
  pads_ares_ensure_block "$file" GameBoy Game.Boy Controls "$bindings"
  local before
  before="$(cat "$file")"
  pads_ares_ensure_block "$file" GameBoy Game.Boy Controls "$bindings"
  [ "$(cat "$file")" = "$before" ]
}

@test "a sibling port's pad block is never credited to an empty container" {
  # Port 1 has a container line but no pad; port 2 has the pad. A scan whose
  # scope flags stick past the sibling reads this as "port 1 complete", the
  # rewrite finds nothing to edit, and the launch claims a pad it never bound.
  local file="$TEST_TMP/sibling/settings.bml"
  mkdir -p "$TEST_TMP/sibling"
  cat >"$file" <<'BML'
SuperFamicom
  Input
    Controller.Port.1
    Controller.Port.2
      Gamepad
        Up: OTHER;;
BML
  local bindings='{"Up": "MINE;;"}'
  pads_ares_ensure_block "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"
  pads_ares_rewrite "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"

  # Port 1 got its own pad and value; port 2 kept its own.
  run awk '/^    Controller.Port.1$/,/^    Controller.Port.2$/' "$file"
  [[ "$output" == *"Up: MINE;;"* ]]
  grep -q "Up: OTHER;;" "$file"
  run grep -c "Up: MINE;;" "$file"
  [ "$output" = "1" ]
}

@test "an Input block in a sibling section is not an anchor" {
  # The console has a non-Input child whose subtree contains a line shaped
  # exactly like our block; the insertion must not land there.
  local file="$TEST_TMP/decoy/settings.bml"
  mkdir -p "$TEST_TMP/decoy"
  cat >"$file" <<'BML'
SuperFamicom
  Hotkeys
    Controller.Port.1
  Input
BML
  local bindings='{"Up": "V;;"}'
  pads_ares_ensure_block "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"

  # The new block sits under Input, not under Hotkeys.
  run awk '/^  Input$/,0' "$file"
  [[ "$output" == *"    Controller.Port.1"* ]]
  [[ "$output" == *"      Gamepad"* ]]
  run awk '/^  Hotkeys$/,/^  Input$/' "$file"
  [[ "$output" != *"Gamepad"* ]]
}

@test "a console with settings but no Input gets one beside them" {
  local file="$TEST_TMP/settings.bml"
  cat >"$file" <<'BML'
GameBoy
  Video
    ColorEmulation: true
Nintendo64
  Input
    Controller.Port.1
      Gamepad
        Up: ;;
BML
  local bindings='{"Up": "NEW;;"}'
  pads_ares_ensure_block "$file" GameBoy Game.Boy Controls "$bindings"
  pads_ares_rewrite "$file" GameBoy Game.Boy Controls "$bindings"

  grep -q "ColorEmulation: true" "$file"
  run awk '/^GameBoy$/,/^Nintendo64$/' "$file"
  [[ "$output" == *"Up: NEW;;"* ]]
  run grep -c "Up: NEW;;" "$file"
  [ "$output" = "1" ]
  run grep -cx "GameBoy" "$file"
  [ "$output" = "1" ]
}

@test "a port that exists without its pad gets one, keeping its siblings" {
  local file="$TEST_TMP/settings.bml"
  cat >"$file" <<'BML'
SuperFamicom
  Input
    Controller.Port.1
      Justifier
        Trigger: ;;
BML
  local bindings='{"Up": "NEW;;"}'
  pads_ares_ensure_block "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"
  pads_ares_rewrite "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"

  grep -q "Trigger: ;;" "$file"
  grep -qx "        Up: NEW;;" "$file"
  run grep -cx "    Controller.Port.1" "$file"
  [ "$output" = "1" ]
}

@test "a console whose name prefixes another is told apart, both ways" {
  local file="$TEST_TMP/settings.bml"
  cat >"$file" <<'BML'
FamicomDiskSystem
  Input
    Controller.Port.1
      Gamepad
        Up: DISK;;
BML
  local bindings='{"Up": "NEW;;"}'
  pads_ares_ensure_block "$file" Famicom Controller.Port.1 Gamepad "$bindings"
  pads_ares_rewrite "$file" Famicom Controller.Port.1 Gamepad "$bindings"

  run grep -cx "Famicom" "$file"
  [ "$output" = "1" ]
  run grep -cx "FamicomDiskSystem" "$file"
  [ "$output" = "1" ]
  run awk '$0 == "Famicom",0' "$file"
  [[ "$output" == *"Up: NEW;;"* ]]
  [[ "$output" != *"DISK"* ]]

  local other="$TEST_TMP/other.bml"
  printf 'Famicom\n  Input\n    Controller.Port.1\n      Gamepad\n        Up: FC;;\n' >"$other"
  pads_ares_ensure_block "$other" FamicomDiskSystem Controller.Port.1 Gamepad "$bindings"
  run grep -cx "FamicomDiskSystem" "$other"
  [ "$output" = "1" ]
  grep -q "Up: FC;;" "$other"
}

@test "a section ares wrote itself is recognised whole and never reshaped" {
  # After one run ares heals a skeleton into its full section — more inputs
  # than any skeleton, sibling pads beside it. Ensuring on top of that must
  # change nothing, byte for byte, and the rewrite must still land.
  local file="$TEST_TMP/settings.bml"
  cat >"$file" <<'BML'
SuperFamicom
  Input
    Controller.Port.1
      Gamepad
        Up: OLD;;
        Down: ;;
        B: ;;
        Select: ;;
        Start: ;;
      Rumble.Gamepad
        Up: ;;
    Controller.Port.2
      Gamepad
        Up: ;;
BML
  local bindings='{"Up": "NEW;;"}' before
  before="$(cat "$file")"
  pads_ares_ensure_block "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"
  [ "$(cat "$file")" = "$before" ]

  pads_ares_rewrite "$file" SuperFamicom Controller.Port.1 Gamepad "$bindings"
  run grep -c "Up: NEW;;" "$file"
  [ "$output" = "1" ]
  grep -q "Start: ;;" "$file"
}

@test "empty bindings refuse to seed a section rather than writing a husk" {
  local file="$TEST_TMP/none/settings.bml"
  run pads_ares_ensure_block "$file" GameBoy Game.Boy Controls '{}'
  [ "$status" -ne 0 ]
  [ ! -e "$file" ]
  [ ! -e "$TEST_TMP/none" ]
}

@test "four controllers seed four ports under one Input" {
  local file="$TEST_TMP/n64/settings.bml" port bindings
  for port in 1 2 3 4; do
    bindings="{\"Up\": \"PAD$port;;\"}"
    pads_ares_ensure_block "$file" Nintendo64 "Controller.Port.$port" Gamepad "$bindings"
    pads_ares_rewrite "$file" Nintendo64 "Controller.Port.$port" Gamepad "$bindings"
  done
  run grep -cx "Nintendo64" "$file"
  [ "$output" = "1" ]
  run grep -cx "  Input" "$file"
  [ "$output" = "1" ]
  for port in 1 2 3 4; do
    grep -qx "    Controller.Port.$port" "$file"
    grep -q "Up: PAD$port;;" "$file"
  done
}

@test "a file whose last line lacks a newline still gets a clean section" {
  # A truncated or hand-edited settings.bml must not have the console name
  # glued onto its last line.
  local file="$TEST_TMP/settings.bml"
  printf 'Video\n  Driver: OpenGL' >"$file"
  pads_ares_ensure_block "$file" GameBoy Game.Boy Controls '{"Up": "X;;"}'
  grep -qx "  Driver: OpenGL" "$file"
  grep -qx "GameBoy" "$file"
}

# The scar that blacked out N64 on the Deck: one launch where GL could not
# initialize and ares saved "Driver: None", keeping the screen black after
# the cause was fixed. Healed on the way into every launch.

heal_fixture() {
  local driver="$1" audio="${2:-SDL}"
  mkdir -p "$GOTG_STATE_DIR/env/env-n64/data/ares"
  cat >"$GOTG_STATE_DIR/env/env-n64/data/ares/settings.bml" <<BML
Video
  Driver: $driver
  Monitor: Primary
Audio
  Driver: $audio
  Latency: 60
BML
  printf '%s/env/env-n64/data/ares/settings.bml' "$GOTG_STATE_DIR"
}

@test "a saved video driver of None is healed to OpenGL at launch" {
  local file
  file="$(heal_fixture None)"
  run ares_heal_video env-n64
  [ "$status" -eq 0 ]
  grep -q "^  Driver: OpenGL 3.2$" "$file"
  [[ "$output" == *"restored"* ]]
}

@test "healing the video driver leaves an audio driver of None alone" {
  local file
  file="$(heal_fixture None None)"
  ares_heal_video env-n64
  [ "$(sed -n '/^Audio/,$p' "$file" | grep '  Driver:')" = "  Driver: None" ]
}

@test "a working video driver is not rewritten" {
  local file before
  file="$(heal_fixture "OpenGL 3.2")"
  before="$(stat -c '%i' "$file")"
  run ares_heal_video env-n64
  [ "$status" -eq 0 ]
  [ "$(stat -c '%i' "$file")" = "$before" ]
  [ -z "$output" ]
}

@test "no settings file — a first launch — is left alone" {
  run ares_heal_video env-n64
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_STATE_DIR/env/env-n64/data/ares/settings.bml" ]
}
