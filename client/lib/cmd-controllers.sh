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
  apply [<id>|--all]      write emulator bindings now, without launching

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
    return 0
  fi

  jq -r '.[] |
    "\(.name)\n" +
    "  binds as   \(.identity)/\(.slot)\n" +
    "  reached by \(if .evdev then .evdev else (.path // "?") + " (raw HID, no evdev node)" end)\n" +
    "  mapping    \(if .map == null then "none — SDL does not recognise this pad, so nothing can be generated for it" else "\(.map | length) elements" end)"
  ' <<<"$pads" >&2
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
