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
      # A trigger rests at one end and travels one way; a stick axis is bound
      # per direction by the element that names it.
      local qualifier=Hi
      case "$element" in
        *up | *left) qualifier=Lo ;;
      esac
      printf '%s/%s/0/%s/%s' "$identity" "$slot" "$index" "$qualifier"
      ;;
    *) return 1 ;;
  esac
}

# Rewrite the Gamepad block of one console's port, leaving every other line of
# the file exactly as it was.
#
# settings.bml is ares' whole configuration and it rewrites the file on exit, so
# this touches only the lines it is replacing. The block is found by walking the
# indentation — Console / Input / Controller.Port.N / Gamepad — because sibling
# blocks (Rumble.Gamepad, Mouse) carry the same button names and must not be
# caught.
pads_ares_rewrite() {
  local file="$1" console="$2" port="$3" bindings="$4"

  local tmp="$file.gotg-tmp"
  jq -r 'to_entries[] | "\(.key)\t\(.value)"' <<<"$bindings" >"$file.gotg-map"

  awk -v console="$console" -v port="$port" -v mapfile="$file.gotg-map" '
    BEGIN {
      while ((getline line < mapfile) > 0) {
        split(line, f, "\t")
        want[f[1]] = f[2]
      }
    }
    # Depth of a line, in ares two-space levels.
    { indent = match($0, /[^ ]/) - 1 }

    indent == 0 { inConsole = ($0 == console); inInput = 0; inPort = 0; inPad = 0 }
    inConsole && indent == 2 { inInput = ($0 ~ /^  Input$/); inPort = 0; inPad = 0 }
    inInput && indent == 4 { inPort = ($0 ~ "^    Controller\\.Port\\." port "$"); inPad = 0 }
    inPort && indent == 6 { inPad = ($0 ~ /^      Gamepad$/) }

    inPad && indent == 8 {
      key = $0
      sub(/^ +/, "", key)
      sub(/:.*$/, "", key)
      if (key in want) {
        printf "        %s: %s;;\n", key, want[key]
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

# Bind player one of an environment to the first gamepad SDL reports.
#
# Never fatal. A launch with no controller attached, or with one SDL does not
# recognise, is a launch on the keyboard — which is worse than a bound pad and
# very much better than not starting.
pads_configure() {
  local attr="$1" manifest console file pads first identity slot map table

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
  [[ -f "$file" ]] || return 0

  # ares writes a console's section the first time that console runs, so the
  # very first launch of a platform has nothing to bind into. Say so, rather
  # than looking like it worked — the next launch will take.
  grep -qx "$console" "$file" || {
    log "ares has not run $console yet — its bindings go in on the next launch"
    return 0
  }

  pads="$("$(pads_bin)" 2>/dev/null)" || return 0
  first="$(jq -c '[.[] | select(.gamepad and .map != null)][0] // empty' <<<"$pads")"
  [[ -n "$first" ]] || return 0

  identity="$(jq -r '.identity' <<<"$first")"
  slot="$(jq -r '.slot' <<<"$first")"
  map="$(jq -c '.map' <<<"$first")"

  local bindings='{}' button element assignment bound=0
  while IFS=$'\t' read -r button element; do
    [[ -n "$button" ]] || continue
    assignment="$(pads_ares_assignment "$identity" "$slot" "$map" "$element")" || continue
    bindings="$(jq -c --arg k "$button" --arg v "$assignment" '. + {($k): $v}' <<<"$bindings")"
    bound=$((bound + 1))
  done < <(jq -r --arg c "$console" '.[$c] | to_entries[] | "\(.key)\t\(.value)"' "$table")

  ((bound > 0)) || return 0

  if pads_ares_rewrite "$file" "$console" 1 "$bindings"; then
    log "bound $bound $console input(s) to $(jq -r '.name' <<<"$first")"
  else
    warn "could not write controller bindings for $attr"
  fi
}
