#!/usr/bin/env bats
# The installer's decisions, without it installing anything.
#
# It is sourced rather than run: every step is a function and only the last
# line does anything, so the machine it thinks it is on, the password it knows
# it needs and the work it knows it can skip are all answerable here. What
# cannot be tested without a Steam Deck is the part that hands over /nix, and
# that is exactly the part that must never run by accident — so nothing here
# calls it.

bats_require_minimum_version 1.5.0

setup() {
  INSTALLER="${BATS_TEST_DIRNAME}/../../install.sh"
  [ -f "$INSTALLER" ] || INSTALLER="$(command -v gotg-install)"
  TMP="$BATS_TEST_TMPDIR"
  export GOTG_INSTALL_LIB=1
  export GOTG_OS_RELEASE="$TMP/os-release"
  export GOTG_UDEV_PATH="$TMP/99-gotg-uinput.rules"
  export XDG_CONFIG_HOME="$TMP/config"
}

steamos() { printf 'ID=steamos\nID_LIKE=arch\nNAME="SteamOS"\n' >"$GOTG_OS_RELEASE"; }
other_linux() { printf 'ID=ubuntu\nID_LIKE=debian\nNAME="Ubuntu"\n' >"$GOTG_OS_RELEASE"; }

load_installer() {
  # shellcheck disable=SC1090
  source "$INSTALLER"
}

@test "it is a shell script, not an AppImage or a Flatpak" {
  # The thing being installed is Nix. Recorded as a test because it is the
  # first question anybody asks of an installer, and the answer is a decision.
  # The shebang's path is whatever packaged it -- the point is that there is
  # one, and that it names a shell.
  run head -1 "$INSTALLER"
  [[ "$output" == "#!"*"bash" ]]
  run file --mime-type -b "$INSTALLER"
  [[ "$output" == text/* ]]
}

@test "SteamOS is recognised by its os-release, not its hostname" {
  steamos
  load_installer
  run is_steamos
  [ "$status" -eq 0 ]
}

@test "a desktop with a deck user is not SteamOS" {
  other_linux
  load_installer
  run is_steamos
  [ "$status" -ne 0 ]
}

@test "a machine with no os-release at all is not SteamOS" {
  rm -f "$GOTG_OS_RELEASE"
  load_installer
  run is_steamos
  [ "$status" -ne 0 ]
}

@test "flakes are turned on, and only once" {
  other_linux
  load_installer
  stub_nix  # the real one on this machine may have flakes on system-wide
  run ensure_flakes
  [ "$status" -eq 0 ]
  grep -q "experimental-features = nix-command flakes" "$XDG_CONFIG_HOME/nix/nix.conf"

  # Again: the file must not grow a second copy.
  run ensure_flakes
  [ "$status" -eq 0 ]
  [ "$(grep -c "experimental-features" "$XDG_CONFIG_HOME/nix/nix.conf")" -eq 1 ]
  [[ "$output" == *"already done"* ]]
}

@test "an existing flakes line is left alone" {
  other_linux
  load_installer
  mkdir -p "$XDG_CONFIG_HOME/nix"
  printf 'experimental-features = nix-command flakes repl-flake\n' >"$XDG_CONFIG_HOME/nix/nix.conf"
  run ensure_flakes
  [ "$status" -eq 0 ]
  grep -q "repl-flake" "$XDG_CONFIG_HOME/nix/nix.conf"
}

@test "a commented-out flakes line does not count as on" {
  other_linux
  load_installer
  stub_nix  # the real one on this machine may have flakes on system-wide
  mkdir -p "$XDG_CONFIG_HOME/nix"
  printf '# experimental-features = nix-command flakes\n' >"$XDG_CONFIG_HOME/nix/nix.conf"
  run ensure_flakes
  [ "$status" -eq 0 ]
  [ "$(grep -cE '^experimental-features' "$XDG_CONFIG_HOME/nix/nix.conf")" -eq 1 ]
}

@test "a Deck with no password is told to set one, rather than shown a sudo error" {
  # `deck` ships with no password, so sudo cannot prompt and fails with a
  # message about the terminal that explains nothing.
  steamos
  load_installer
  sudo() { return 1; }
  passwd() { printf '%s NP 01/01/2024 0 99999 7 -1\n' "$USER"; }
  export -f sudo passwd 2>/dev/null || true
  run need_sudo
  [ "$status" -ne 0 ]
  [[ "$output" == *"passwd"* ]]
  [[ "$output" == *"no password"* ]]
}

@test "the udev rule names uinput and hands it to whoever is at the seat" {
  other_linux
  load_installer
  [[ "$UDEV_RULE" == *'KERNEL=="uinput"'* ]]
  [[ "$UDEV_RULE" == *'uaccess'* ]]
}

@test "a writable uinput means there is nothing to do" {
  other_linux
  load_installer
  # The check is on /dev/uinput itself; where a machine already allows it, the
  # installer must not ask for a password to change nothing.
  if [[ -w /dev/uinput ]]; then
    run ensure_uinput
    [ "$status" -eq 0 ]
    [[ "$output" == *"already done"* ]]
  else
    skip "/dev/uinput is not writable here, which is the case the rule is for"
  fi
}

@test "the flake it installs from can be pointed somewhere else" {
  other_linux
  GOTG_FLAKE_REF="git+file:///somewhere/else" load_installer
  [ "$FLAKE" = "git+file:///somewhere/else" ]
}

@test "sourcing it installs nothing" {
  # The guard that makes every test above safe. If this ever fails, the rest
  # of this file is running an installer on somebody's machine.
  steamos
  run load_installer
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_UDEV_PATH" ]
  [ ! -e "$XDG_CONFIG_HOME/nix/nix.conf" ]
}

# --- installing GOTG itself ----------------------------------------------------
#
# A stand-in `nix` answers `profile list --json` from $NIX_HAVE and records every
# call, so each case is a machine in a known state and nothing really installs.

stub_nix() {
  NIX_CALLS="$TMP/nix-calls"
  : >"$NIX_CALLS"
  nix() {
    printf '%s\n' "$*" >>"$NIX_CALLS"
    case "$*" in
      "profile list --json")
        local elements="" name
        for name in ${NIX_HAVE:-}; do
          elements+="${elements:+,}\"$name\":{}"
        done
        printf '{"version":3,"elements":{%s}}\n' "$elements"
        ;;
      "config show experimental-features") printf '%s\n' "${NIX_FEATURES:-}" ;;
      "--version") printf 'nix (Nix) %s\n' "${NIX_VERSION:-2.30.0}" ;;
    esac
    return 0
  }
}

@test "a machine that already has GOTG upgrades it rather than adding it again" {
  # The Deck this was tested on: `nix profile add` of a name already present
  # is a warning and exit 0, so the old build stayed forever and the installer
  # said it had installed GOTG.
  other_linux
  load_installer
  stub_nix
  NIX_HAVE="gotg gotg-ui" run install_gotg
  [ "$status" -eq 0 ]
  grep -qx "profile upgrade gotg gotg-ui" "$NIX_CALLS"
  ! grep -q "profile add" "$NIX_CALLS"
}

@test "a fresh machine adds both" {
  other_linux
  load_installer
  stub_nix
  NIX_HAVE="" run install_gotg
  [ "$status" -eq 0 ]
  grep -qx "profile add $FLAKE#gotg $FLAKE#gotg-ui" "$NIX_CALLS"
  ! grep -q "profile upgrade" "$NIX_CALLS"
}

@test "half an install is finished rather than redone" {
  other_linux
  load_installer
  stub_nix
  NIX_HAVE="gotg" run install_gotg
  [ "$status" -eq 0 ]
  grep -qx "profile upgrade gotg" "$NIX_CALLS"
  grep -qx "profile add $FLAKE#gotg-ui" "$NIX_CALLS"
}

@test "flakes turned on system-wide are not written into the user's file too" {
  # The Deck again: a daemon install with flakes in /etc/nix/nix.conf.
  other_linux
  load_installer
  stub_nix
  NIX_FEATURES="nix-command flakes" run ensure_flakes
  [ "$status" -eq 0 ]
  [[ "$output" == *"already done"* ]]
  [ ! -e "$XDG_CONFIG_HOME/nix/nix.conf" ]
}

@test "a dry run changes nothing" {
  other_linux
  load_installer
  stub_nix
  DRY_RUN=1 NIX_HAVE="gotg gotg-ui" run install_gotg
  [ "$status" -eq 0 ]
  ! grep -q "profile upgrade" "$NIX_CALLS"
  ! grep -q "profile add" "$NIX_CALLS"
}

@test "a dry run says what it would have done" {
  other_linux
  load_installer
  stub_nix
  DRY_RUN=1 NIX_HAVE="gotg" run install_gotg
  [[ "$output" == *"would run: nix profile upgrade gotg"* ]]
  [[ "$output" == *"would run: nix profile add"* ]]
}

@test "a dry run writes no nix.conf" {
  other_linux
  load_installer
  stub_nix
  DRY_RUN=1 NIX_FEATURES="nix-command" run ensure_flakes
  [ "$status" -eq 0 ]
  [ ! -e "$XDG_CONFIG_HOME/nix/nix.conf" ]
}

# Records what would have touched the machine, instead of touching it.
stub_side_effects() {
  SIDE="$TMP/side-effects"
  : >"$SIDE"
  sudo() { printf 'sudo %s\n' "$*" >>"$SIDE"; }
  curl() { printf 'curl %s\n' "$*" >>"$SIDE"; }
  gotg() { printf 'gotg %s\n' "$*" >>"$SIDE"; }
  pgrep() { return 1; }
}

@test "a dry run installs no Nix" {
  other_linux
  load_installer
  stub_side_effects
  DRY_RUN=1 run install_nix_generic
  [ "$status" -eq 0 ]
  [ ! -s "$SIDE" ]
  [[ "$output" == *"would run:"* ]]
}

@test "a dry run hands over no /nix on a Deck" {
  steamos
  load_installer
  stub_side_effects
  DRY_RUN=1 run install_nix_steamos
  [ ! -s "$SIDE" ]
}

@test "a dry run writes no udev rule" {
  other_linux
  export GOTG_UINPUT="$TMP/no-such-uinput"
  load_installer
  stub_side_effects
  DRY_RUN=1 run ensure_uinput
  [ "$status" -eq 0 ]
  [ ! -s "$SIDE" ]
  [ ! -e "$GOTG_UDEV_PATH" ]
  [[ "$output" == *"would run:"* ]]
}

@test "a dry run adds no Steam shortcut" {
  other_linux
  load_installer
  stub_side_effects
  DRY_RUN=1 run add_to_steam
  [ "$status" -eq 0 ]
  [ ! -s "$SIDE" ]
  [[ "$output" == *"would run: gotg steam picker"* ]]
}

@test "a failed rule on SteamOS still puts the system partition back to read-only" {
  steamos
  export GOTG_UINPUT="$TMP/no-such-uinput"
  load_installer
  stub_side_effects
  steamos-readonly() { printf 'enabled\n'; }
  # Password already cached, and the write itself refused.
  sudo() {
    printf 'sudo %s\n' "$*" >>"$SIDE"
    [[ "$1" != tee ]]
  }
  run ensure_uinput
  [ "$status" -ne 0 ]
  grep -qx "sudo steamos-readonly disable" "$SIDE"
  grep -qx "sudo steamos-readonly enable" "$SIDE"
}

@test "an exported DRY_RUN from something else does not turn the install into a dry run" {
  other_linux
  export DRY_RUN=1
  load_installer
  [ "$DRY_RUN" = "0" ]
}

# --- how old a Nix this can work with -----------------------------------------
#
# `nix profile add` and upgrading by name both arrived in 2.30. An older Nix
# is told so, rather than handed commands it does not know and a profile
# listing this cannot read.

@test "a Nix older than 2.30 is told to upgrade, not fed commands it lacks" {
  other_linux
  load_installer
  stub_nix
  NIX_VERSION="2.18.1" run check_nix_version
  [ "$status" -ne 0 ]
  [[ "$output" == *"2.18.1"* ]]
  [[ "$output" == *"upgrade-nix"* ]]
}

@test "a new enough Nix passes the version check quietly" {
  other_linux
  load_installer
  stub_nix
  NIX_VERSION="2.35.1" run check_nix_version
  [ "$status" -eq 0 ]
  NIX_VERSION="2.30.0" run check_nix_version
  [ "$status" -eq 0 ]
}

@test "a Nix whose version cannot be read is not turned away" {
  other_linux
  load_installer
  nix() { printf 'nix (Determinate Nix) something-odd\n'; }
  run check_nix_version
  [ "$status" -eq 0 ]
}

@test "a nix that cannot list a profile is treated as an empty one" {
  other_linux
  load_installer
  stub_nix
  nix() { [[ "$*" == "profile list --json" ]] && return 1; return 0; }
  run install_gotg
  [ "$status" -eq 0 ]
  [[ "$output" == *"installing GOTG"* ]]
}

# --- main's own arguments ------------------------------------------------------
#
# Never reached by sourcing, but still defined, so it is called here with its
# steps replaced by nothing.

stub_steps() {
  ensure_nix() { :; }
  ensure_flakes() { :; }
  install_gotg() { :; }
  ensure_uinput() { :; }
  add_to_steam() { :; }
}

@test "-n is --dry-run, and the steps see it" {
  other_linux
  load_installer
  stub_steps
  ensure_nix() { printf 'ensure_nix DRY_RUN=%s\n' "$DRY_RUN"; }
  run main -n
  [ "$status" -eq 0 ]
  [[ "$output" == *"ensure_nix DRY_RUN=1"* ]]
  [[ "$output" == *"Dry run done"* ]]
}

@test "--help prints usage and runs no step" {
  other_linux
  load_installer
  ensure_nix() { echo "SHOULD NOT RUN"; }
  run main --dry-run --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage: install.sh"* ]]
  [[ "$output" != *"SHOULD NOT RUN"* ]]
}

@test "an unknown option dies before touching anything" {
  other_linux
  load_installer
  ensure_nix() { echo "SHOULD NOT RUN"; }
  run main --bogus
  [ "$status" -eq 1 ]
  [[ "$output" == *"unknown option: --bogus"* ]]
  [[ "$output" != *"SHOULD NOT RUN"* ]]
}

# --- the udev rule's other branches --------------------------------------------

@test "a rule already on disk but not yet live says reboot and asks for nothing" {
  other_linux
  export GOTG_UINPUT="$TMP/no-such-uinput"
  load_installer
  printf '%s\n' "$UDEV_RULE" >"$GOTG_UDEV_PATH"
  stub_side_effects
  run ensure_uinput
  [ "$status" -eq 0 ]
  [[ "$output" == *"reboot"* ]]
  [ ! -s "$SIDE" ]
}

@test "a dry run on a locked Deck unlocks and relocks only on paper" {
  steamos
  export GOTG_UINPUT="$TMP/no-such-uinput"
  load_installer
  stub_side_effects
  steamos-readonly() { printf 'enabled\n'; }
  DRY_RUN=1 run ensure_uinput
  [ "$status" -eq 0 ]
  [ ! -s "$SIDE" ]
  [[ "$output" == *"would run: sudo steamos-readonly disable"* ]]
  [[ "$output" == *"would run: sudo steamos-readonly enable"* ]]
}

@test "a refused rule write on a desktop is an error, not a silent Done" {
  other_linux
  export GOTG_UINPUT="$TMP/no-such-uinput"
  load_installer
  stub_side_effects
  sudo() { [[ "$1" != tee ]]; }
  run ensure_uinput
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not write"* ]]
}

@test "GOTG_DRY_RUN in the environment is honoured" {
  other_linux
  export GOTG_DRY_RUN=1
  load_installer
  [ "$DRY_RUN" = "1" ]
}

# --- the Steam shortcut, with Steam open ----------------------------------------
#
# Steam rewrites its shortcut file when it exits and reads it once at start,
# so the picker can only be added while Steam is closed. The usual Deck has
# Steam open in Desktop Mode, and the installer used to say "queued" and
# queue nothing: GOTG never reached the library.

# A Steam that is open, as a script rather than a function: the relaunch goes
# through setsid, which runs a program, and a function would be a real Steam.
steam_open() {
  export STEAM_STATE="$TMP/steam-state"
  printf 'running\n' >"$STEAM_STATE"
  pgrep() { [[ "$*" == *steam* ]] && [[ "$(cat "$STEAM_STATE")" == running ]]; }
  export GOTG_STEAM_BIN="$TMP/bin/steam"
  mkdir -p "$TMP/bin"
  cat >"$GOTG_STEAM_BIN" <<EOF
#!$(command -v bash)
printf 'steam%s\n' "\${*:+ \$*}" >>"$SIDE"
[[ "\${1:-}" == -shutdown ]] && printf 'closed\n' >"$STEAM_STATE"
exit 0
EOF
  chmod +x "$GOTG_STEAM_BIN"
  STEAM_BIN="$GOTG_STEAM_BIN"
}

@test "with Steam open and a person to ask, Steam is closed, the picker added, Steam started again" {
  other_linux
  load_installer
  stub_side_effects
  steam_open
  confirm() { return 0; }
  STEAM_WAIT=5 run add_to_steam
  [ "$status" -eq 0 ]
  # The relaunch is detached, so give it a moment to be recorded.
  for _ in 1 2 3 4 5 6 7 8 9 10; do grep -qx "steam" "$SIDE" && break; sleep 0.2; done
  [ "$(grep -E '^(steam|gotg)' "$SIDE" | tr '\n' ';')" = "steam -shutdown;gotg steam picker;steam;" ]
}

@test "with Steam open and no answer, the shortcut is queued and the way to apply it said" {
  other_linux
  load_installer
  stub_side_effects
  steam_open
  confirm() { return 1; }
  run add_to_steam
  [ "$status" -eq 0 ]
  grep -qx "gotg steam picker" "$SIDE"
  ! grep -q "steam -shutdown" "$SIDE"
  [[ "$output" == *"queued"* ]]
  [[ "$output" == *"gotg steam picker"* ]]
}

@test "with Steam closed the picker is simply added" {
  other_linux
  load_installer
  stub_side_effects
  run add_to_steam
  [ "$status" -eq 0 ]
  [ "$(grep -E '^(steam|gotg)' "$SIDE" | tr '\n' ';')" = "gotg steam picker;" ]
}

@test "a Steam that will not close in time is not waited on for ever" {
  other_linux
  load_installer
  stub_side_effects
  steam_open
  confirm() { return 0; }
  printf '#!%s\nprintf "steam %%s\\n" "$*" >>"%s"\nexit 0\n' "$(command -v bash)" "$SIDE" >"$GOTG_STEAM_BIN"  # never actually closes
  STEAM_WAIT=1 run add_to_steam
  [ "$status" -eq 0 ]
  [[ "$output" == *"did not close"* ]]
  grep -qx "gotg steam picker" "$SIDE"
}

@test "a dry run with Steam open says what it would do to Steam and does nothing" {
  other_linux
  load_installer
  stub_side_effects
  steam_open
  DRY_RUN=1 run add_to_steam
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run: $GOTG_STEAM_BIN -shutdown"* ]]
  [[ "$output" == *"would run: gotg steam picker"* ]]
  [[ "$output" == *"would run: $GOTG_STEAM_BIN"* ]]
  [ ! -s "$SIDE" ]
}

@test "the question about closing Steam is actually put on the terminal" {
  # It was not, and the shape of the bug was a hang: `read -p` writes its
  # prompt to stderr, the redirection hiding "no /dev/tty" hid the prompt
  # with it, and the installer sat waiting for an answer to a question
  # nobody had been asked. A terminal is needed to catch that, so this runs
  # the thing under a pty rather than calling it directly.
  load_installer
  run script -qec \
    "bash -c 'GOTG_INSTALL_LIB=1 . $INSTALLER; \
      printf y | confirm \"Close Steam now?\"'" /dev/null
  [[ "$output" == *"Close Steam now?"* ]]
  [[ "$output" == *"[Y/n]"* ]]
}

@test "with no terminal to ask, the answer is no and nothing is said about it" {
  load_installer
  # setsid detaches from the controlling terminal, which is the machine this
  # has to answer for: a service, a Steam-launched shell, anything piped.
  run setsid bash -c \
    "GOTG_INSTALL_LIB=1 . $INSTALLER; \
     confirm 'Close Steam now?' && echo YES || echo NO" </dev/null
  [[ "$output" == *"NO"* ]]
  [[ "$output" != *"No such device"* ]]
}

@test "an upgrade rebuilds the environments already here" {
  # A newer gotg launching yesterday's environments is the Deck after every
  # upgrade: the roots only ever caught up when somebody ran gotg sync.
  other_linux
  load_installer
  stub_nix
  stub_side_effects
  NIX_HAVE="gotg gotg-ui" run install_gotg
  [ "$status" -eq 0 ]
  grep -qx "gotg sync" "$SIDE"
  # A first install has nothing to catch up.
  : >"$SIDE"
  NIX_HAVE="" run install_gotg
  ! grep -q "gotg sync" "$SIDE"
}
