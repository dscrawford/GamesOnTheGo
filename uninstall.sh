#!/usr/bin/env bash
# Take GOTG off a machine. The inverse of install.sh, minus Nix.
#
#   curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/uninstall.sh | bash
#
# Removes the two commands from the Nix profile, the picker's Steam shortcut
# and the udev rule install.sh wrote. Games and saves stay unless --games is
# given, and Nix stays regardless: it is a package manager, not part of GOTG,
# and removing it is one command this prints at the end for whoever wants it.
set -euo pipefail

OS_RELEASE="${GOTG_OS_RELEASE:-/etc/os-release}"
UDEV_PATH="${GOTG_UDEV_PATH:-/etc/udev/rules.d/99-gotg-uinput.rules}"
STATE_DIR="${GOTG_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/gotg}"
CONFIG_DIR="${GOTG_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/gotg}"
# How Nix got here, for the removal command said at the end. Overridable so
# the tests can be each kind of machine in turn.
NIX_INSTALLER_RECEIPT="${GOTG_NIX_INSTALLER_RECEIPT:-/nix/nix-installer}"
NIX_DAEMON_UNIT="${GOTG_NIX_DAEMON_UNIT:-/etc/systemd/system/nix-daemon.socket}"

C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_DIM=$'\e[2m'; C_OFF=$'\e[0m'
[[ -t 1 ]] || { C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""; }

say() { printf '%s\n' "$*" >&2; }
step() { printf '%s==>%s %s\n' "$C_OK" "$C_OFF" "$*" >&2; }
skip() { printf '%s==>%s %s %s(nothing to do)%s\n' "$C_DIM" "$C_OFF" "$*" "$C_DIM" "$C_OFF" >&2; }
warn() { printf '%s!!%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die() { printf '%serror:%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

# Every change goes through here, so --dry-run can show them instead.
DRY_RUN="${GOTG_DRY_RUN:-0}"
change() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '%s   would run:%s %s\n' "$C_DIM" "$C_OFF" "$*" >&2
    return 0
  fi
  "$@"
}

is_steamos() {
  [[ -f "$OS_RELEASE" ]] && grep -qE '^ID=steamos$' "$OS_RELEASE"
}

readonly_root() {
  command -v steamos-readonly >/dev/null 2>&1 || return 1
  [[ "$(steamos-readonly status 2>/dev/null || echo enabled)" != "disabled" ]]
}

need_sudo() {
  [[ "$DRY_RUN" != "1" ]] || { say "   would ask for your password"; return 0; }
  command -v sudo >/dev/null 2>&1 || die "no sudo here, and removing the udev rule needs root"
  sudo -n true 2>/dev/null && return 0
  say ""
  say "  The next step needs your password."
  say ""
  sudo -v || die "sudo said no"
}

# Which of the two the profile holds, one per line.
installed() {
  nix profile list --json 2>/dev/null |
    grep -oE '"(gotg|gotg-ui)":' | tr -d '":' | sort -u || true
}

# --- the steps ---------------------------------------------------------------

remove_from_steam() {
  command -v gotg >/dev/null 2>&1 || { skip "no gotg here to ask about Steam"; return 0; }
  step "taking GOTG out of your Steam library"
  change gotg steam remove picker || warn "could not remove the Steam shortcut; run: gotg steam remove picker"
}

remove_gotg() {
  command -v nix >/dev/null 2>&1 || { skip "no nix here, so nothing is installed"; return 0; }
  local have=()
  mapfile -t have < <(installed)
  if ((${#have[@]} == 0)); then
    skip "GOTG is not in the Nix profile"
    return 0
  fi
  step "removing ${have[*]} from the Nix profile"
  change nix profile remove "${have[@]}" || die "could not remove ${have[*]}"
}

remove_uinput_rule() {
  if [[ ! -f "$UDEV_PATH" ]]; then
    skip "no udev rule to remove"
    return 0
  fi
  step "removing the controller permission ($UDEV_PATH)"
  need_sudo
  local relock=0
  if readonly_root; then
    change sudo steamos-readonly disable || die "could not unlock the system partition"
    relock=1
    if [[ "$DRY_RUN" != "1" ]]; then
      trap 'sudo steamos-readonly enable || warn "the system partition is still writable; run: sudo steamos-readonly enable"' EXIT
      trap 'exit 130' INT TERM
    fi
  fi
  change sudo rm -f "$UDEV_PATH" || die "could not remove $UDEV_PATH"
  change sudo udevadm control --reload-rules || true
  if ((relock)); then
    trap - EXIT INT TERM
    change sudo steamos-readonly enable || warn "could not put the system partition back to read-only"
  fi
}

remove_games() {
  local removed=0 dir
  for dir in "$STATE_DIR" "$CONFIG_DIR"; do
    [[ -e "$dir" ]] || continue
    step "deleting $dir"
    change rm -rf "$dir"
    removed=1
  done
  ((removed)) || skip "no games, saves or settings on this machine"
}

# Nix is left where it is. The one command that takes it away, for the
# machine this is, said rather than run: it is not GOTG's to remove.
nix_removal() {
  command -v nix >/dev/null 2>&1 || return 0
  say ""
  say "  Nix is still installed. It is a package manager, not part of GOTG, and"
  say "  other things may use it. To remove it as well:"
  say ""
  if [[ -x "$NIX_INSTALLER_RECEIPT" ]]; then
    # The Determinate installer, which is what a recent SteamOS gets.
    say "    sudo $NIX_INSTALLER_RECEIPT uninstall"
  elif [[ -e "$NIX_DAEMON_UNIT" ]]; then
    say "    https://nix.dev/manual/nix/stable/installation/uninstall#multi-user"
    say "    (the daemon install has no single command; that page has the six)"
  else
    say "    sudo rm -rf /nix ~/.nix-profile ~/.nix-defexpr ~/.nix-channels ~/.config/nix"
    say "    and delete the 'nix.sh' line its installer added to ~/.profile or ~/.bashrc"
  fi
  say ""
}

main() {
  local games=0 arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run | -n) DRY_RUN=1 ;;
      --games) games=1 ;;
      -h | --help)
        say "usage: uninstall.sh [--dry-run] [--games]"
        say "  --dry-run   print what would change, change nothing"
        say "  --games     also delete downloaded games, saves and settings"
        exit 0
        ;;
      *) die "unknown option: $arg (try --help)" ;;
    esac
  done
  [[ "$DRY_RUN" != "1" ]] || say "  (dry run: nothing will be changed)"
  say ""
  say "  GOTG — removing from $(is_steamos && echo "SteamOS" || echo "this machine")"
  say ""

  remove_from_steam
  remove_gotg
  remove_uinput_rule
  if ((games)); then
    remove_games
  elif [[ -e "$STATE_DIR" ]]; then
    say "   games and saves are kept in $STATE_DIR; --games deletes them"
  fi

  say ""
  if [[ "$DRY_RUN" == "1" ]]; then
    say "${C_OK}Dry run done. Nothing was changed.${C_OFF}"
  else
    say "${C_OK}Done.${C_OFF} Restart Steam and the shortcut is gone."
  fi
  nix_removal
}

# Sourced rather than run when the tests want the decisions without the
# removals: every step is a function, and only this line does anything.
[[ "${GOTG_INSTALL_LIB:-0}" == "1" ]] && return 0
main "$@"
