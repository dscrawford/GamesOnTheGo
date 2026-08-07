# shellcheck shell=bash
# `gotg controllers` — where the inputs land.
#
# Deliberately *not* about making a controller work at the system level. Whether
# this machine can read a given device is the host's business: an evdev pad
# needs nothing, and one that talks raw HID needs a udev rule that arrives with
# programs.steam.enable or the steam-devices package. Shipping our own copy of
# that was duplicating the distribution's job.
#
# What is ours is the half above it: which controller SDL is looking at, and
# which emulator input each of its buttons ends up driving.

controllers_usage() {
  cat <<'EOF'
usage: gotg controllers <command> [args]

  list                    the controllers SDL can see, as the emulators see them
  order                   who is player 1, player 2, and so on
  order --set <pad>...    choose that order; anything unnamed follows behind
  order --clear           go back to the order SDL enumerates them
  order --json            the same answer, for a UI rather than a person
  apply [<id>|--all]      write emulator bindings now, without launching

A controller can be named by any part of its name or by the identity/slot the
bindings are keyed on. Naming one is enough to make it player 1.

Bindings are also written on every `gotg play`, so this is for checking a change
or fixing a controller up without starting a game.

Making a device readable in the first place is the host's job. An ordinary pad
needs nothing; one that talks raw HID (the Steam Controller) needs the
steam-devices udev rules, which programs.steam.enable already installs.
EOF
}

cmd_controllers() {
  local verb="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  case "$verb" in
    list) controllers_list "$@" ;;
    order) controllers_order "$@" ;;
    apply) controllers_apply "$@" ;;
    help | --help | -h | "") controllers_usage ;;
    *)
      printf 'error: unknown controllers command: %s\n\n' "$verb" >&2
      controllers_usage >&2
      exit 1
      ;;
  esac
}

# What SDL reports, in the terms the bindings are written in. `identity/slot` is
# the string an emulator stores, so seeing it here is how you tell whether a
# controller is the same one a binding was written for.
controllers_list() {
  local pads
  pads="$("$(pads_bin)" 2>/dev/null)" || die "could not run $(pads_bin)"

  if [[ "$(jq 'length' <<<"$pads")" == "0" ]]; then
    log "no controllers visible to SDL."
    log ""
    log "An ordinary pad needs no setup. A Steam Controller talks raw HID and"
    log "needs the steam-devices udev rules — programs.steam.enable installs"
    log "them, as does the steam-devices package on other distributions."
    log ""
    log "A Steam Controller also needs Steam itself to be running. Without it the"
    log "pad stays in lizard mode, acting as a keyboard and mouse, and nothing"
    log "here can see it as a controller however the rules are set."
    return 0
  fi

  # A pad SDL cannot map is the one thing here worth interrupting for, so it is
  # the only line that takes a colour of its own.
  jq -r --arg h "$C_HEAD" --arg m "$C_MUTED" --arg r "$C_RESET" --arg w "$C_WARN" '.[] |
    "\($h)\(.name)\($r)\n" +
    "  \($m)binds as  \($r) \(.identity)/\(.slot)\n" +
    "  \($m)reached by\($r) \(if .evdev then .evdev else (.path // "?") + " (raw HID, no evdev node)" end)\n" +
    "  \($m)mapping   \($r) \(if .map == null then "\($w)none — SDL does not recognise this pad, so nothing can be generated for it\($r)" else "\(.map | length) elements" end)"
  ' <<<"$pads" >&2
}

# Who gets which console port.
#
# Decided by SDL's enumeration order, which is also what the bindings are
# written from, so this and what the emulator does cannot disagree. It is worth
# a command of its own because the answer is invisible otherwise until someone
# presses start on the wrong pad.
controllers_order() {
  case "${1:-}" in
    --set)
      shift
      controllers_order_set "$@"
      return
      ;;
    --clear)
      controllers_order_clear
      return
      ;;
    --json)
      controllers_order_json
      return
      ;;
    -*) die "unknown option for controllers order: $1" ;;
  esac

  local pads seating count
  pads="$("$(pads_bin)" 2>/dev/null)" || die "could not run $(pads_bin)"
  seating="$(pads_seating "$pads")"
  count="$(jq 'length' <<<"$seating")"

  if [[ "$count" == "0" ]]; then
    log "no controllers to seat."
    log ""
    log "A pad SDL does not recognise cannot be bound and so is not seated."
    log "Run: gotg controllers list"
    return 0
  fi

  jq -r --argjson max "$GOTG_MAX_PLAYERS" \
    --arg h "$C_HEAD" --arg m "$C_MUTED" --arg r "$C_RESET" --arg w "$C_WARN" '
    to_entries[] |
    if .key < $max then
      "  \($h)Player \(.key + 1)\($r)   \(.value.name)\n             \($m)\(.value.identity)/\(.value.slot)\($r)"
    else
      "  \($w)(unseated)\($r) \(.value.name) — consoles here have only \($max) ports"
    end' <<<"$seating" >&2

  log ""
  if [[ "$(jq 'length' <<<"$(pads_order_read)")" == "0" ]]; then
    log "Order follows the order SDL enumerates them, which is what the emulators"
    log "go by too. To choose instead:"
    log ""
    log "  gotg controllers order --set xbox      # that pad first, rest behind it"
  else
    log "This order is pinned, in $(pads_order_file)."
    log "Anything not named there follows in SDL's order."
    log ""
    log "  gotg controllers order --clear         # back to SDL's order"
  fi
  log ""
  log "Bindings are rewritten on the next launch, or now with:"
  log "  gotg controllers apply --all"
}

# One controller, named either exactly as the bindings key it or by any part of
# its name. A UI would pass the key; a person types "xbox".
#
# Prints the key, or fails. `die` inside a command substitution exits only that
# subshell, so every caller checks the status rather than trusting the trap.
controllers_resolve() {
  local seating="$1" want="$2" matches count
  matches="$(jq -c --arg w "$want" '
    [ .[] | select(
        "\(.identity)/\(.slot)" == $w
        or ((.name | ascii_downcase) | contains($w | ascii_downcase))
      ) ]' <<<"$seating")"
  count="$(jq 'length' <<<"$matches")"

  case "$count" in
    0) die "no controller here matches \"$want\" — run: gotg controllers order" ;;
    1) jq -r '.[0] | "\(.identity)/\(.slot)"' <<<"$matches" ;;
    *)
      warn "\"$want\" matches $count controllers:"
      jq -r '.[] | "  \(.name)  \(.identity)/\(.slot)"' <<<"$matches" >&2
      die "name one of them exactly, or use its identity/slot"
      ;;
  esac
}

# Pin an order. Naming some of the controllers is enough — the rest keep SDL's
# order behind the ones named, which is what makes "put the Xbox pad first" a
# one-word command rather than a full list.
controllers_order_set() {
  [[ $# -gt 0 ]] ||
    die "usage: gotg controllers order --set <controller> [<controller>...]"

  local pads seating
  pads="$("$(pads_bin)" 2>/dev/null)" || die "could not run $(pads_bin)"
  seating="$(pads_seating "$pads")"
  [[ "$(jq 'length' <<<"$seating")" != "0" ]] ||
    die "no controllers to order — run: gotg controllers list"

  local keys='[]' want key
  for want in "$@"; do
    key="$(controllers_resolve "$seating" "$want")" || exit 1
    # Naming one twice would have it take two seats and push a real pad out.
    jq -e --arg k "$key" 'index($k) == null' >/dev/null <<<"$keys" ||
      die "\"$want\" is already in the order"
    keys="$(jq -c --arg k "$key" '. + [$k]' <<<"$keys")"
  done

  local file
  file="$(pads_order_file)"
  mkdir -p "$GOTG_CONFIG_DIR"
  if ! jq -n --argjson order "$keys" '{order: $order}' >"$file.tmp"; then
    rm -f "$file.tmp"
    die "could not write $file"
  fi
  mv "$file.tmp" "$file" || die "could not write $file"

  controllers_order
}

controllers_order_clear() {
  local file
  file="$(pads_order_file)"
  if [[ ! -f "$file" ]]; then
    log "nothing was pinned — SDL's order already stands"
    return 0
  fi
  rm -f "$file"
  log "cleared: back to the order SDL enumerates them"
  log ""
  log "Bindings are rewritten on the next launch, or now with:"
  log "  gotg controllers apply --all"
}

# The same answer, for something other than a person to read. Everything else
# here logs to stderr, so this has stdout to itself.
controllers_order_json() {
  local pads seating order
  pads="$("$(pads_bin)" 2>/dev/null)" || die "could not run $(pads_bin)"
  seating="$(pads_seating "$pads")"
  order="$(pads_order_read)"

  jq --argjson order "$order" --argjson max "$GOTG_MAX_PLAYERS" '
    { pinned: ($order | length) > 0,
      ports: $max,
      players: [ to_entries[] | {
        player: (.key + 1),
        seated: (.key < $max),
        name: .value.name,
        key: "\(.value.identity)/\(.value.slot)",
        pinned: ("\(.value.identity)/\(.value.slot)" as $k
                 | ($order | index($k)) != null)
      } ] }' <<<"$seating"
}

controllers_apply() {
  local want="${1:-}"
  local attrs=()

  if [[ -z "$want" || "$want" == "--all" ]]; then
    local root name
    [[ -d "$GOTG_ROOTS_DIR" ]] || die "nothing is built here yet"
    for root in "$GOTG_ROOTS_DIR"/*; do
      [[ -e "$root" ]] || continue
      name="$(basename "$root")"
      [[ "$name" =~ $GOTG_ATTR_RE ]] || continue
      [[ -f "$(env_pads_manifest "$name")" ]] || continue
      attrs+=("$name")
    done
  else
    manifest_cached || manifest_ensure
    local game
    game="$(manifest_find "$want")"
    attrs+=("$(env_attr "$game")")
  fi

  [[ ${#attrs[@]} -gt 0 ]] ||
    die "no environment here generates bindings — see client/data/ares-pads.json"

  local attr
  for attr in "${attrs[@]}"; do
    pads_configure "$attr" || warn "could not write bindings for $attr"
  done
}
