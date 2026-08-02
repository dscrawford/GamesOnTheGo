# shellcheck shell=bash
# `gotg controllers` — the one step controllers need that a package cannot do
# for itself.
#
# Almost all of controller support is unprivileged: an Xbox pad works out of the
# box because systemd already grants access to joystick devices. The exception is
# anything that talks over raw HID rather than evdev — the current Steam
# Controller is one — where reading /dev/hidraw* needs a udev rule. That rule is
# the entire privileged surface, and it is one store path.
#
# On NixOS this is the wrong tool entirely; see below.

# Only the input rules. The package also carries VR rules, which are nothing to
# do with playing a Mega Drive game.
GOTG_CONTROLLER_RULES=(60-steam-input.rules)

# Seams, so this is testable without being root or being on the host it targets.
udev_rules_dir() { printf '%s' "${GOTG_UDEV_RULES_DIR:-/etc/udev/rules.d}"; }
nixos_marker() { printf '%s' "${GOTG_NIXOS_MARKER:-/etc/NIXOS}"; }

controllers_usage() {
  cat <<'EOF'
usage: gotg controllers <command> [args]

  install-rules [--apply]   let this machine read controllers that talk raw HID

Without --apply it prints the commands and changes nothing. Xbox pads need none
of this. On NixOS, use the module instead:

  imports = [ gotg.nixosModules.controllers ];
  programs.gotg.controllers.enable = true;
EOF
}

cmd_controllers() {
  local verb="${1:-}"
  [[ $# -gt 0 ]] && shift || true
  case "$verb" in
    install-rules) controllers_install_rules "$@" ;;
    help | --help | -h | "") controllers_usage ;;
    *)
      printf 'error: unknown controllers command: %s\n\n' "$verb" >&2
      controllers_usage >&2
      exit 1
      ;;
  esac
}

# The store path holding the rules, built from the flake through the same seam
# env.sh builds environments with.
controllers_rules_path() {
  local flake ref out
  flake="$(gotg_flake)"
  [[ -f "$flake/flake.nix" ]] ||
    die "no flake at $flake, so there is nothing to take the rules from.
     Point at your checkout with GOTG_FLAKE, or the 'flake' key in $GOTG_CONFIG_FILE."

  ref="$flake#controller-udev-rules"
  out="$("$(nix_bin)" build "$ref" --no-link --print-out-paths)" ||
    die "could not build $ref"
  [[ -n "$out" ]] || die "building $ref produced no path"
  printf '%s' "$out"
}

controllers_install_rules() {
  local apply="no" arg
  for arg in "$@"; do
    case "$arg" in
      --apply) apply="yes" ;;
      *) die "unknown option: $arg (only --apply)" ;;
    esac
  done

  # Symlinking into /etc/udev/rules.d on NixOS is not merely unnecessary, it is
  # actively wrong: the next rebuild replaces that directory and the rule
  # silently goes away, which is a worse failure than never having worked.
  if [[ -e "$(nixos_marker)" ]]; then
    die "this is NixOS, where a hand-made symlink in $(udev_rules_dir) is replaced
     on the next rebuild. Use the module instead:

       inputs.gotg.url = \"path:$(gotg_flake)\";
       imports = [ inputs.gotg.nixosModules.controllers ];
       programs.gotg.controllers.enable = true;"
  fi

  local store dir rule src dest
  store="$(controllers_rules_path)"
  dir="$(udev_rules_dir)"

  local todo=()
  for rule in "${GOTG_CONTROLLER_RULES[@]}"; do
    src="$store/lib/udev/rules.d/$rule"
    dest="$dir/$rule"
    [[ -f "$src" ]] || die "$rule is not in $store — the rules package has changed shape"

    if [[ "$(readlink -f "$dest" 2>/dev/null)" == "$src" ]]; then
      log "$dest already points at the current rules"
      continue
    fi
    todo+=("$rule")
  done

  if [[ ${#todo[@]} -eq 0 ]]; then
    log "nothing to do — this machine is already set up."
    return 0
  fi

  # The commands go to stdout so they can be read, copied, or piped; everything
  # explaining them goes to stderr, so a pipe gets only the commands.
  if [[ "$apply" != "yes" ]]; then
    log "These need root. Run them yourself, or re-run with --apply:"
    log ""
    for rule in "${todo[@]}"; do
      printf 'sudo ln -sfn %s %s\n' "$store/lib/udev/rules.d/$rule" "$dir/$rule"
    done
    printf 'sudo udevadm control --reload && sudo udevadm trigger\n'
    log ""
    log "Then replug the controller."
    return 0
  fi

  need_cmd sudo
  for rule in "${todo[@]}"; do
    log "linking $rule into $dir"
    sudo ln -sfn "$store/lib/udev/rules.d/$rule" "$dir/$rule" ||
      die "could not link $rule into $dir"
  done

  log "reloading udev"
  sudo udevadm control --reload || die "could not reload udev"
  sudo udevadm trigger || die "could not trigger udev"

  log ""
  log "Done. Replug the controller — a rule only applies to devices that arrive"
  log "after it does."
}
