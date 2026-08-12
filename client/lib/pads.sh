# shellcheck shell=bash
# Writing an emulator's controller bindings, so nobody has to press every button
# in a settings screen once per console per environment.
#
# Three things have to line up, and each is read rather than assumed:
#
#   - which controller ares thinks it is looking at. That is
#     "<identity>/<slot>", built by ruby/input/joypad/sdl.cpp and reproduced by
#     gotg-pads, and compared as a string — so a difference of any kind is a
#     controller that silently has no buttons.
#   - which raw input drives each standard button. SDL knows, per controller
#     model, and gotg-pads asks it. This is what stops the table below needing a
#     column per pad.
#   - how ares spells that. Groups are enumerated in nall/hid.hpp — Axis 0,
#     Hat 1, Trigger 2, Button 3 — and a hat is split into a pair of pseudo-axes,
#     X then Y, each with a Lo/Hi qualifier. Confirmed against bindings ares
#     wrote itself, not inferred.

pads_bin() { printf '%s' "${GOTG_PADS:-gotg-pads}"; }

ares_pads_table() { printf '%s/ares-pads.json' "$GOTG_DATA"; }

# One ares assignment for a standard element, or nothing when this controller
# has no such element — a pad without a right stick simply leaves those unbound
# rather than being refused.
#
#   $1 identity  $2 slot  $3 the device's map, as JSON  $4 SDL element name
pads_ares_assignment() {
  local identity="$1" slot="$2" map="$3" element="$4"

  # An axis element carries its direction as a trailing sign, because the name
  # alone does not have one: "lefty" is a whole stick, and only "lefty-" is up.
  local sign=""
  case "$element" in
    *-)
      sign=Lo
      element="${element%-}"
      ;;
    *+)
      sign=Hi
      element="${element%+}"
      ;;
  esac

  local entry
  entry="$(jq -c --arg e "$element" '.[$e] // empty' <<<"$map")"
  [[ -n "$entry" ]] || return 1

  local type index
  type="$(jq -r '.type' <<<"$entry")"
  index="$(jq -r '.index' <<<"$entry")"

  case "$type" in
    button)
      printf '%s/%s/3/%s' "$identity" "$slot" "$index"
      ;;
    hat)
      # SDL gives a direction mask: 1 up, 2 right, 4 down, 8 left. ares wants
      # hat N as inputs 2N and 2N+1 — X then Y — with Lo for up and left.
      local mask axis qualifier
      mask="$(jq -r '.mask' <<<"$entry")"
      case "$mask" in
        1) axis=$((index * 2 + 1)) qualifier=Lo ;;
        4) axis=$((index * 2 + 1)) qualifier=Hi ;;
        8) axis=$((index * 2)) qualifier=Lo ;;
        2) axis=$((index * 2)) qualifier=Hi ;;
        *) return 1 ;;
      esac
      printf '%s/%s/1/%s/%s' "$identity" "$slot" "$axis" "$qualifier"
      ;;
    axis)
      # A trigger rests at one end and travels one way, so it needs no sign; a
      # stick axis is bound one direction at a time and the sign says which.
      local qualifier="${sign:-Hi}"
      printf '%s/%s/0/%s/%s' "$identity" "$slot" "$index" "$qualifier"
      ;;
    *) return 1 ;;
  esac
}

# Rewrite one pad block, leaving every other line of the file exactly as it was.
#
# settings.bml is ares' whole configuration and it rewrites the file on exit, so
# this touches only the lines it is replacing. The block is found by walking the
# indentation — Console / Input / <container> / <pad> — because sibling blocks
# (Rumble.Gamepad, Mouse, Justifier) carry the same input names and must not be
# caught. The container is "Controller.Port.1" on a console with ports and
# "Game.Boy" on a handheld, which is the whole of the difference between them.
#
# Inputs one level deeper are addressed as "<block>/<name>": ares spells a true
# analog axis as an X-Axis block containing Lo and Hi, and those lines are worth
# reaching, since binding them is what makes a stick analog rather than four
# switches.
# The key lines a fresh pad block needs, one per binding, unbound. A nested
# "Axis/Sub" key becomes its axis block with the sub-input inside — jq sorts
# keys, so an axis's subkeys arrive together and the axis header is emitted
# once.
pads_ares_skeleton_keys() {
  local bindings="$1" key axis last_axis=""
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    if [[ "$key" == */* ]]; then
      axis="${key%%/*}"
      if [[ "$axis" != "$last_axis" ]]; then
        printf '        %s\n' "$axis"
        last_axis="$axis"
      fi
      printf '          %s: ;;\n' "${key#*/}"
    else
      printf '        %s: ;;\n' "$key"
      last_axis=""
    fi
  done < <(jq -r 'keys[]' <<<"$bindings")
}

# Make sure the block the rewrite will edit actually exists.
#
# ares only creates a console's section after that console has run, which made
# the first launch of every new platform silently padless — visible at scale
# the day two whole libraries arrived at once. The file is plain indented
# text, so the missing levels are written here instead: ares merges an
# unknown-but-well-formed section on load, and rewrites the file completely on
# exit, so a skeleton holding only the inputs this pad could bind heals into
# ares' own full section after one run.
pads_ares_ensure_block() {
  local file="$1" console="$2" block="$3" pad="$4" bindings="$5"

  # What already exists, scoped exactly as the rewrite scopes it — checked
  # before anything is built, because this runs on every launch and the
  # everything-present answer is the common one.
  local state="0000"
  if [[ -f "$file" ]]; then
    # ENVIRON rather than -v: -v values undergo C-escape processing, so a
    # backslash in a table key would become a real newline inside the program.
    # The reset-per-level pattern mirrors the rewrite exactly — a flag that
    # sticks past a sibling line credits one port with another's pad block.
    state="$(c="$console" b="    $block" p="      $pad" awk '
      BEGIN { c = ENVIRON["c"]; b = ENVIRON["b"]; p = ENVIRON["p"] }
      { indent = match($0, /[^ ]/) - 1 }
      indent == 0 { inC = ($0 == c); if (inC) hc = 1; inI = 0; inB = 0 }
      inC && indent == 2 { inI = ($0 == "  Input"); inB = 0; if (inI) hi = 1 }
      inI && indent == 4 { inB = ($0 == b); if (inB) hb = 1 }
      inB && indent == 6 && $0 == p { hp = 1 }
      END { printf "%d%d%d%d", hc + 0, hi + 0, hb + 0, hp + 0 }
    ' "$file")" || return 1
    [[ "$state" == "1111" ]] && return 0
  fi

  # Before any filesystem write, so a refusal leaves nothing behind.
  local keys
  keys="$(pads_ares_skeleton_keys "$bindings")"
  [[ -n "$keys" ]] || return 1

  # The missing tail, and which existing line it slots in after. Inserting
  # directly after the parent line only reorders siblings, which bml does not
  # care about.
  local payload anchor
  case "$state" in
    0???)
      mkdir -p "$(dirname "$file")"
      # A truncated file must not have the console name glued onto its last
      # line — ares would carry the mangled setting forward forever.
      if [[ -s "$file" && "$(tail -c1 "$file")" != $'\n' ]]; then
        printf '\n' >>"$file"
      fi
      printf '%s\n  Input\n    %s\n      %s\n%s\n' \
        "$console" "$block" "$pad" "$keys" >>"$file"
      return 0
      ;;
    10??) payload="  Input"$'\n'"    $block"$'\n'"      $pad"$'\n'"$keys" anchor="console" ;;
    110?) payload="    $block"$'\n'"      $pad"$'\n'"$keys" anchor="input" ;;
    111?) payload="      $pad"$'\n'"$keys" anchor="block" ;;
    *) return 1 ;;
  esac

  local tmp
  tmp="$(mktemp "$file.XXXXXX")" || return 1
  c="$console" b="    $block" anchor="$anchor" payload="$payload" awk '
    BEGIN {
      c = ENVIRON["c"]; b = ENVIRON["b"]
      anchor = ENVIRON["anchor"]; payload = ENVIRON["payload"]
    }
    { indent = match($0, /[^ ]/) - 1 }
    indent == 0 { inC = ($0 == c); inI = 0 }
    inC && indent == 2 { inI = ($0 == "  Input") }
    { print }
    !done && inC && anchor == "console" && indent == 0 { print payload; done = 1 }
    !done && inI && anchor == "input" && indent == 2 { print payload; done = 1 }
    !done && inI && anchor == "block" && indent == 4 && $0 == b { print payload; done = 1 }
  ' "$file" >"$tmp" || {
    rm -f "$tmp"
    return 1
  }
  mv "$tmp" "$file"
}

pads_ares_rewrite() {
  local file="$1" console="$2" block="$3" pad="$4" bindings="$5"

  local tmp="$file.gotg-tmp"
  jq -r 'to_entries[] | "\(.key)\t\(.value)"' <<<"$bindings" >"$file.gotg-map"

  awk -v console="$console" -v block="$block" -v pad="$pad" -v mapfile="$file.gotg-map" '
    BEGIN {
      while ((getline line < mapfile) > 0) {
        split(line, f, "\t")
        want[f[1]] = f[2]
      }
    }
    # Depth of a line, in ares two-space levels.
    { indent = match($0, /[^ ]/) - 1 }

    indent == 0 { inConsole = ($0 == console); inInput = 0; inBlock = 0; inPad = 0; axis = "" }
    inConsole && indent == 2 { inInput = ($0 ~ /^  Input$/); inBlock = 0; inPad = 0; axis = "" }
    inInput && indent == 4 { inBlock = ($0 == "    " block); inPad = 0; axis = "" }
    inBlock && indent == 6 { inPad = ($0 == "      " pad); axis = "" }

    inPad && indent == 8 {
      key = $0
      sub(/^ +/, "", key)
      if (key ~ /:/) {
        # An ordinary input. It also ends whatever nested block preceded it.
        axis = ""
        sub(/:.*$/, "", key)
        if (key in want) {
          printf "        %s: %s\n", key, want[key]
          next
        }
      } else {
        # A block rather than an input: X-Axis, Y-Axis.
        axis = key
      }
    }

    inPad && indent == 10 && axis != "" {
      key = $0
      sub(/^ +/, "", key)
      sub(/:.*$/, "", key)
      if ((axis "/" key) in want) {
        printf "          %s: %s\n", key, want[axis "/" key]
        next
      }
    }
    { print }
  ' "$file" >"$tmp" || {
    rm -f "$tmp" "$file.gotg-map"
    return 1
  }

  mv "$tmp" "$file"
  rm -f "$file.gotg-map"
}

# Every ares console tops out at four controller ports.
GOTG_MAX_PLAYERS=4

# How many bindings ares keeps per input. They are joined by ";", which is why
# an unbound input reads ";;" — three slots, all of them empty.
GOTG_ARES_BINDINGS=3

# One controller's worth of bindings, as ares input -> the value to write.
#
# The value is the whole field, separators and all, because how many of the
# three slots are filled is decided here: an input can be driven by the D-pad
# and the stick at once.
pads_ares_bindings() {
  local console="$1" identity="$2" slot="$3" map="$4"
  local bindings='{}' input elements
  while IFS=$'\t' read -r input elements; do
    [[ -n "$input" ]] || continue

    local value="" filled=0 element assignment
    # Word splitting is what is wanted: the elements arrive space-separated.
    # shellcheck disable=SC2086
    for element in $elements; do
      ((filled < GOTG_ARES_BINDINGS)) || break
      assignment="$(pads_ares_assignment "$identity" "$slot" "$map" "$element")" || continue
      value+="${value:+;}$assignment"
      ((filled++))
    done
    # An element this controller lacks binds nothing rather than guessing, and
    # an input whose every element is missing is left as ares had it.
    ((filled > 0)) || continue
    while ((filled < GOTG_ARES_BINDINGS)); do
      value+=";"
      ((filled++))
    done

    bindings="$(jq -c --arg k "$input" --arg v "$value" '. + {($k): $v}' <<<"$bindings")"
  done < <(
    jq -r --arg c "$console" '
      .[$c].buttons | to_entries[]
      | "\(.key)\t\(if (.value | type) == "array" then (.value | join(" ")) else .value end)"
    ' "$(ares_pads_table)"
  )
  printf '%s' "$bindings"
}

# Where a chosen order is remembered.
#
# Its own file rather than a key in config.json: that one holds the server
# password and is kept 0600, and which pad is player 1 is neither a secret nor
# worth rewriting a credentials file over.
pads_order_file() { printf '%s/controllers.json' "$GOTG_CONFIG_DIR"; }

# The pinned order, as identity/slot keys. Absent, unreadable and malformed all
# mean the same thing — no preference — because a config file that cannot be
# parsed is a reason to fall back to SDL's order, not to refuse to launch.
pads_order_read() {
  local file
  file="$(pads_order_file)"
  [[ -f "$file" ]] || {
    printf '[]'
    return 0
  }
  jq -c '.order // []' "$file" 2>/dev/null || printf '[]'
}

# The controllers that can be bound, in the order they will be seated.
#
# SDL's enumeration order decides it unless something has been pinned, and
# every caller comes through here — both emulators and `gotg controllers order`
# — so what is displayed and what is written cannot disagree.
#
# A pinned controller that is not attached simply is not there to seat, and the
# ones behind it move up. Anything unpinned follows in SDL's order.
pads_seating() {
  local order
  order="$(pads_order_read)"
  jq -c --argjson order "$order" '
    [ .[] | select(.gamepad and .map != null) ]
    | map(. + { _key: "\(.identity)/\(.slot)" })
    | map(. + { _rank: (._key as $k | $order | index($k)) })
    | ( [ .[] | select(._rank != null) ] | sort_by(._rank) )
      + [ .[] | select(._rank == null) ]
    | map(del(._key) | del(._rank))
  ' <<<"$1"
}

# Bind each attached controller, whichever emulator this environment runs.
#
# The environment says which, in the pads.json its derivation carries; an
# environment that says nothing is one whose bindings are left alone.
pads_configure() {
  local attr="$1" manifest emulator

  manifest="$(env_pads_manifest "$attr")"
  [[ -f "$manifest" ]] || return 0
  emulator="$(jq -r '.emulator // "ares"' "$manifest")"

  case "$emulator" in
    ares) pads_ares_configure "$attr" ;;
    dolphin) pads_dolphin_configure "$attr" ;;
    ryujinx) pads_ryujinx_configure "$attr" ;;
    *) return 0 ;;
  esac
}

# Bind each attached controller to the ares console port of the same number.
#
# Never fatal. A launch with no controller attached, or with one SDL does not
# recognise, is a launch on the keyboard — which is worse than a bound pad and
# very much better than not starting.
pads_ares_configure() {
  local attr="$1" manifest console file pads identity slot map table

  manifest="$(env_pads_manifest "$attr")"
  [[ -f "$manifest" ]] || return 0
  console="$(jq -r '.console // empty' "$manifest")"
  [[ -n "$console" ]] || return 0

  table="$(ares_pads_table)"
  [[ -f "$table" ]] || return 0
  jq -e --arg c "$console" 'has($c)' >/dev/null 2>&1 <"$table" || {
    warn "no button table for $console — leaving its bindings alone"
    return 0
  }

  file="$(env_state_dir "$attr")/data/ares/settings.bml"

  pads="$("$(pads_bin)" 2>/dev/null)" || return 0

  # Where this console keeps its pads. A handheld has one built in and no port
  # number; everything else numbers them from 1.
  local container padBlock players
  container="$(jq -r --arg c "$console" '.[$c].layout.container' "$table")"
  padBlock="$(jq -r --arg c "$console" '.[$c].layout.pad' "$table")"
  players="$(jq -r --arg c "$console" '.[$c].layout.players // 1' "$table")"

  local seating count
  seating="$(pads_seating "$pads")"
  count="$(jq 'length' <<<"$seating")"
  ((count > 0)) || return 0
  ((count <= GOTG_MAX_PLAYERS)) || count=$GOTG_MAX_PLAYERS
  # A second controller on a Game Boy has nowhere to sit.
  ((count <= players)) || count=$players

  local player port bindings name block where
  for ((player = 0; player < count; player++)); do
    port=$((player + 1))
    identity="$(jq -r ".[$player].identity" <<<"$seating")"
    slot="$(jq -r ".[$player].slot" <<<"$seating")"
    map="$(jq -c ".[$player].map" <<<"$seating")"
    name="$(jq -r ".[$player].name" <<<"$seating")"

    if ((players > 1)); then
      block="$container.$port"
      where="$console port $port"
    else
      block="$container"
      where="$console"
    fi

    bindings="$(pads_ares_bindings "$console" "$identity" "$slot" "$map")"
    [[ "$(jq 'length' <<<"$bindings")" != "0" ]] || continue

    # ares has not run this console yet? Then the section it would have made
    # is made here, so the very first launch already has a working pad.
    pads_ares_ensure_block "$file" "$console" "$block" "$padBlock" "$bindings" || {
      warn "could not prepare the $console bindings section for $attr"
      continue
    }

    if pads_ares_rewrite "$file" "$console" "$block" "$padBlock" "$bindings"; then
      log "player $port: $name -> $where ($(jq 'length' <<<"$bindings") inputs)"
    else
      warn "could not write player $port's bindings for $attr"
    fi
  done
}
