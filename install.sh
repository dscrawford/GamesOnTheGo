#!/usr/bin/env bash
# Put GOTG on a machine that has never heard of Nix.
#
#   curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh | bash
#
# Or, on a Steam Deck, download it and run it from Konsole in Desktop Mode.
#
# Not an AppImage and not a Flatpak, deliberately: what is being installed is
# *Nix*. A Flatpak is sandboxed and cannot create /nix, write a udev rule or
# reach Steam's shortcut file, and an AppImage bundles an application with its
# libraries -- this application's libraries are the Nix store. See docs/install.md.
#
# Idempotent: running it twice does nothing the second time. It needs a password
# for three things, each said before it asks -- /nix on SteamOS, Nix's own
# installer elsewhere, and making /dev/uinput openable so padmap can publish
# controllers.
set -euo pipefail

FLAKE="${GOTG_FLAKE_REF:-github:dscrawford/GamesOnTheGo}"
NIX_INSTALLER="${GOTG_NIX_INSTALLER:-https://nixos.org/nix/install}"
# Overridable so the tests can put a SteamOS in front of it, and so a rule can
# be written somewhere harmless. Nothing else has a reason to set them.
OS_RELEASE="${GOTG_OS_RELEASE:-/etc/os-release}"
UDEV_PATH="${GOTG_UDEV_PATH:-/etc/udev/rules.d/99-gotg-uinput.rules}"
UINPUT="${GOTG_UINPUT:-/dev/uinput}"

C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_DIM=$'\e[2m'; C_OFF=$'\e[0m'
[[ -t 1 ]] || { C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""; }

say() { printf '%s\n' "$*" >&2; }
step() { printf '%s==>%s %s\n' "$C_OK" "$C_OFF" "$*" >&2; }
skip() { printf '%s==>%s %s %s(already done)%s\n' "$C_DIM" "$C_OFF" "$*" "$C_DIM" "$C_OFF" >&2; }
warn() { printf '%s!!%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die() { printf '%serror:%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

# Every change goes through here, so --dry-run can show them instead.
# Namespaced: a DRY_RUN some other tool left exported must not quietly skip it.
DRY_RUN="${GOTG_DRY_RUN:-0}"
change() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '%s   would run:%s %s\n' "$C_DIM" "$C_OFF" "$*" >&2
    return 0
  fi
  "$@"
}

# --- what machine is this ----------------------------------------------------

# SteamOS, by the file every SteamOS release has had. Not by hostname, and not
# by the presence of a `deck` user: somebody's desktop can have both.
is_steamos() {
  [[ -e "$OS_RELEASE" ]] || return 1
  grep -qiE '^(ID|ID_LIKE)=.*steamos' "$OS_RELEASE"
}

# The read-only root is SteamOS's, but the tool is what actually decides.
readonly_root() {
  command -v steamos-readonly >/dev/null 2>&1 || return 1
  [[ "$(steamos-readonly status 2>/dev/null || echo enabled)" != "disabled" ]]
}

# --- sudo --------------------------------------------------------------------

# A Steam Deck ships with no password for `deck`, so sudo cannot ask for one
# and every prompt fails with a message about the terminal. Telling somebody
# that up front is the difference between a minute and an afternoon.
need_sudo() {
  if [[ "$DRY_RUN" == "1" ]]; then
    say "   would ask for your password"
    return 0
  fi
  command -v sudo >/dev/null 2>&1 || die "no sudo here, and this needs root for one or two steps"
  if sudo -n true 2>/dev/null; then
    return 0
  fi
  if is_steamos && ! passwd --status "$USER" 2>/dev/null | grep -qE ' (P|PS) '; then
    die "this account has no password, so sudo cannot ask for one.
     Set one first — run: passwd
     then run this again. (It is the Deck's own password, not your Steam one.)"
  fi
  say ""
  say "  The next step needs your password."
  say ""
  sudo -v || die "sudo said no"
}

# --- nix ---------------------------------------------------------------------

have_nix() { command -v nix >/dev/null 2>&1 || [[ -e /nix/var/nix/profiles/default/bin/nix ]]; }

# Put nix on PATH for the rest of this run. A fresh single-user install only
# reaches the shell that sources its profile, and this one did not.
use_nix() {
  local single="$HOME/.nix-profile/etc/profile.d/nix.sh"
  local daemon=/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
  # shellcheck disable=SC1090,SC1091 # written by the installer, not by us
  [[ -r "$single" ]] && . "$single"
  # shellcheck disable=SC1090,SC1091
  [[ -r "$daemon" ]] && . "$daemon"
  command -v nix >/dev/null 2>&1
}

# A pipe cannot go through `change`, so the download and the run are one command.
nix_installer() { curl --proto '=https' --tlsv1.2 -fsSL "$NIX_INSTALLER" | sh -s -- "$@"; }

install_nix_steamos() {
  # SteamOS 3.5 and later ship /nix already, bind-mounted to the home
  # partition, and Valve keeps it across updates — which is the whole reason
  # this works at all. It belongs to root until somebody hands it over.
  if [[ ! -d /nix ]]; then
    die "no /nix on this SteamOS, so it is older than 3.5.
     Update SteamOS and run this again — before 3.5 the store had to be
     bind-mounted by hand, and that is not something to do behind your back."
  fi
  if [[ ! -w /nix ]]; then
    step "handing /nix to $USER"
    need_sudo
    change sudo chown "$USER" /nix
  fi
  step "installing Nix (single user — no daemon, nothing to keep running)"
  change nix_installer --no-daemon
}

install_nix_generic() {
  step "installing Nix"
  need_sudo
  change nix_installer --daemon
}

# `nix profile add`, and upgrading a profile entry by name, both arrived in
# 2.30. An older Nix would be handed commands it does not know.
NIX_MIN="2.30"
check_nix_version() {
  local found major minor
  found="$(nix --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
  if [[ -z "$found" ]]; then
    warn "could not read this Nix's version; continuing as if it were new enough"
    return 0
  fi
  IFS=. read -r major minor _ <<<"$found"
  if ((major < ${NIX_MIN%.*} || (major == ${NIX_MIN%.*} && minor < ${NIX_MIN#*.}))); then
    die "Nix $found is older than $NIX_MIN, which this needs.
     Upgrade it and run this again: nix upgrade-nix
     (with sudo on a machine that runs the Nix daemon)"
  fi
}

ensure_nix() {
  if have_nix && use_nix; then
    skip "Nix is here"
    check_nix_version
    return 0
  fi
  if is_steamos; then install_nix_steamos; else install_nix_generic; fi
  use_nix || die "Nix installed but is not on PATH. Open a new terminal and run this again."
}

# Flakes are still behind a flag, and GOTG is a flake.
ensure_flakes() {
  local conf="${XDG_CONFIG_HOME:-$HOME/.config}/nix/nix.conf"
  # Asked of nix, not read from one file: a daemon install (the Determinate
  # installer's, for one) turns flakes on in /etc/nix/nix.conf.
  if nix config show experimental-features 2>/dev/null | grep -qw flakes ||
    { [[ -f "$conf" ]] && grep -qE '^[^#]*experimental-features.*flakes' "$conf"; }; then
    skip "flakes are on"
    return 0
  fi
  step "turning on flakes"
  change mkdir -p "$(dirname "$conf")"
  if [[ "$DRY_RUN" == "1" ]]; then
    change append "experimental-features = nix-command flakes" to "$conf"
  else
    printf 'experimental-features = nix-command flakes\n' >>"$conf"
  fi
}

# --- controllers -------------------------------------------------------------

# padmap publishes each controller as a new device through /dev/uinput, and
# cannot open it without permission. `uaccess` gives it to whoever is logged in
# at the seat, which is the right answer on a handheld.
UDEV_RULE='KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"'

ensure_uinput() {
  if [[ -w "$UINPUT" ]]; then
    skip "controllers can be published"
    return 0
  fi
  if [[ -f "$UDEV_PATH" ]] && grep -qF 'uinput' "$UDEV_PATH"; then
    warn "the rule is written but /dev/uinput is still not writable — a reboot applies it"
    return 0
  fi

  step "letting GOTG publish controllers (/dev/uinput)"
  need_sudo

  local relock=0
  if readonly_root; then
    # SteamOS keeps / read-only and puts it back on every update, so this rule
    # is not permanent. Said out loud at the end rather than discovered later.
    change sudo steamos-readonly disable || die "could not unlock the system partition"
    relock=1
    if [[ "$DRY_RUN" != "1" ]]; then
      # A failure or Ctrl-C from here on must not leave / writable.
      trap 'sudo steamos-readonly enable || warn "the system partition is still writable; run: sudo steamos-readonly enable"' EXIT
      trap 'exit 130' INT TERM
    fi
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    change write "$UDEV_RULE" to "$UDEV_PATH"
  else
    printf '%s\n' "$UDEV_RULE" | sudo tee "$UDEV_PATH" >/dev/null || die "could not write $UDEV_PATH"
  fi
  change sudo udevadm control --reload-rules || true
  change sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput || true
  change sudo modprobe -q uinput || true
  if ((relock)); then
    trap - EXIT INT TERM
    change sudo steamos-readonly enable || warn "could not put the system partition back to read-only"
  fi
}

# --- gotg --------------------------------------------------------------------

# Which of these the profile already holds, one per line.
installed() {
  nix profile list --json 2>/dev/null |
    grep -oE '"(gotg|gotg-ui)":' | tr -d '":' | sort -u || true
}

install_gotg() {
  local have want=() upgrade=() name
  have="$(installed)"
  for name in gotg gotg-ui; do
    if grep -qx "$name" <<<"$have"; then
      upgrade+=("$name")
    else
      want+=("$FLAKE#$name")
    fi
  done

  # Upgraded, not re-added: adding a name the profile already has is a warning
  # and exit 0, so a re-run left the old build in place and reported success.
  if ((${#upgrade[@]})); then
    step "upgrading ${upgrade[*]}"
    change nix profile upgrade "${upgrade[@]}" || die "could not upgrade ${upgrade[*]}"
    # The environments already built here follow the upgrade now rather than
    # on each game's next launch: a first launch that is also a rebuild is a
    # long wait behind a loader, and a build error is better read here.
    step "rebuilding the environments already here"
    change gotg sync || warn "could not rebuild the environments; each rebuilds on its next launch"
  fi
  if ((${#want[@]})); then
    step "installing GOTG from $FLAKE"
    change nix profile add "${want[@]}" ||
      die "could not install GOTG. For a private repository, set
     NIX_CONFIG=\"extra-access-tokens = github.com=<token>\""
  fi
}

# A yes-or-no put to the person, on the terminal if there is one. Through a
# pipe (curl | bash) stdin is the script itself, so the question goes to
# /dev/tty; with no terminal at all the answer is no.
#
# The prompt is written to /dev/tty rather than passed to `read -p`, which
# looks like the same thing and is not: read writes its prompt to stderr, and
# the redirection that hides "no such device" on a machine without a
# controlling terminal hid the question too. What that looked like was a
# hang -- the installer stopped at "putting GOTG in your Steam library" with
# nothing on screen, waiting for an answer to a question it had never asked.
confirm() {
  local answer
  [[ "$DRY_RUN" != "1" ]] || return 1
  # stderr is silenced before /dev/tty is opened, not after: a machine with no
  # controlling terminal fails the redirection itself, and that complaint is
  # the shell's, printed before any redirection on the same line has taken
  # effect. The other order answers no and says "No such device" while doing it.
  printf '%s [Y/n] ' "$1" 2>/dev/null >/dev/tty || return 1
  read -r answer 2>/dev/null </dev/tty || return 1
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

# How long to give Steam to shut down before giving up on it, and which
# program Steam is -- named so a test can stand one in.
STEAM_WAIT="${STEAM_WAIT:-30}"
STEAM_BIN="${GOTG_STEAM_BIN:-steam}"

# The picker, in Steam's library. Steam rewrites its shortcut file when it
# exits and reads it once at start, so this only lands while Steam is closed
# -- and on a Deck in Desktop Mode it is open. Asked, then: closed, added,
# started again. Declined, or with nobody to ask: gotg queues the shortcut
# itself, and the next `gotg steam` command run with Steam closed applies it.
add_to_steam() {
  # In a dry run gotg may not be installed yet, and is only named.
  [[ "$DRY_RUN" == "1" ]] || command -v gotg >/dev/null 2>&1 || return 0
  step "putting GOTG in your Steam library"
  if ! pgrep -x steam >/dev/null 2>&1; then
    change gotg steam picker || warn "could not add the Steam shortcut; run 'gotg steam picker' yourself"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    say "   Steam is open; it would be closed, GOTG added, and Steam started again"
    change "$STEAM_BIN" -shutdown
    change gotg steam picker
    change "$STEAM_BIN"
    return 0
  fi
  if ! confirm "   Steam is open, and can only take a new entry while closed. Close it now?"; then
    gotg steam picker >/dev/null 2>&1 || true
    warn "the shortcut is queued. Close Steam, run: gotg steam picker
     and start Steam again -- it reads its library once, at startup."
    return 0
  fi

  say "   closing Steam"
  "$STEAM_BIN" -shutdown >/dev/null 2>&1 || true
  local waited=0
  while pgrep -x steam >/dev/null 2>&1 && ((waited < STEAM_WAIT)); do
    sleep 1
    waited=$((waited + 1))
  done
  if pgrep -x steam >/dev/null 2>&1; then
    gotg steam picker >/dev/null 2>&1 || true
    warn "Steam did not close in ${STEAM_WAIT}s; the shortcut is queued. Close Steam, run:
     gotg steam picker, and start Steam again."
    return 0
  fi
  gotg steam picker || warn "could not add the Steam shortcut; run 'gotg steam picker' yourself"
  say "   starting Steam again"
  # Detached: Steam must outlive this script, and its output is its own.
  (setsid "$STEAM_BIN" >/dev/null 2>&1 &)
}

# --- the run -----------------------------------------------------------------

main() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run | -n) DRY_RUN=1 ;;
      -h | --help)
        say "usage: install.sh [--dry-run]"
        say "  --dry-run   print what would change, change nothing"
        exit 0
        ;;
      *) die "unknown option: $arg (try --help)" ;;
    esac
  done
  [[ "$DRY_RUN" == "1" ]] && say "  (dry run: nothing will be changed)"
  say ""
  say "  GOTG — installing onto $(is_steamos && echo "SteamOS" || echo "this machine")"
  say ""

  command -v curl >/dev/null 2>&1 || die "curl is needed and is not here"

  ensure_nix
  ensure_flakes
  install_gotg
  ensure_uinput
  add_to_steam

  say ""
  if [[ "$DRY_RUN" == "1" ]]; then
    say "${C_OK}Dry run done. Nothing was changed.${C_OFF}"
    return 0
  fi
  say "${C_OK}Done.${C_OFF}"
  say ""
  say "  gotg list                 what is in the catalog"
  say "  gotg install <id>         download a game and build what runs it"
  say "  gotg-ui                   the picker, for a controller and a sofa"
  say ""
  if is_steamos; then
    say "  Restart Steam to see GOTG in your library. It reads its shortcut file"
    say "  once, at startup."
    say ""
    say "${C_WARN}  A SteamOS update wipes the system partition, which takes the"
    say "  controller permission with it. Your games and Nix survive — they are on"
    say "  the home partition. Run this installer again after an update to put it"
    say "  back.${C_OFF}"
    say ""
  fi
}

# Sourced rather than run when the tests want the decisions without the
# installing: every step above is a function, and this is the only line that
# does anything.
[[ "${GOTG_INSTALL_LIB:-0}" == "1" ]] && return 0

main "$@"
