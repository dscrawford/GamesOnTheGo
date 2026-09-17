#!/usr/bin/env bats
# uninstall.sh's decisions, without it removing anything: sourced, every step
# a function, and each thing it would touch a stand-in that records the call.

bats_require_minimum_version 1.5.0

setup() {
  UNINSTALLER="${BATS_TEST_DIRNAME}/../../uninstall.sh"
  [ -f "$UNINSTALLER" ] || UNINSTALLER="$(command -v gotg-uninstall)"
  TMP="$BATS_TEST_TMPDIR"
  export GOTG_INSTALL_LIB=1
  export GOTG_OS_RELEASE="$TMP/os-release"
  export GOTG_UDEV_PATH="$TMP/99-gotg-uinput.rules"
  export GOTG_STATE_DIR="$TMP/state/gotg"
  export GOTG_CONFIG_DIR="$TMP/config/gotg"
  printf 'ID=ubuntu\n' >"$GOTG_OS_RELEASE"
  # shellcheck disable=SC1090
  source "$UNINSTALLER"
  CALLS="$TMP/calls"
  : >"$CALLS"
  nix() {
    printf 'nix %s\n' "$*" >>"$CALLS"
    if [[ "$*" == "profile list --json" ]]; then
      local elements="" name
      for name in ${NIX_HAVE:-}; do elements+="${elements:+,}\"$name\":{}"; done
      printf '{"version":3,"elements":{%s}}\n' "$elements"
    fi
  }
  gotg() { printf 'gotg %s\n' "$*" >>"$CALLS"; }
  sudo() { printf 'sudo %s\n' "$*" >>"$CALLS"; [[ "$1" != rm ]] || rm -f "${@: -1}"; }
}

@test "sourcing it removes nothing" {
  [ -z "$(cat "$CALLS")" ]
}

@test "the two commands are removed from the profile, and only the ones there" {
  NIX_HAVE="gotg gotg-ui" run remove_gotg
  [ "$status" -eq 0 ]
  grep -qx "nix profile remove gotg gotg-ui" "$CALLS"

  : >"$CALLS"
  NIX_HAVE="gotg-ui" run remove_gotg
  grep -qx "nix profile remove gotg-ui" "$CALLS"
}

@test "nothing installed is nothing to do, not an error" {
  NIX_HAVE="" run remove_gotg
  [ "$status" -eq 0 ]
  ! grep -q "profile remove" "$CALLS"
  [[ "$output" == *"nothing to do"* ]]
}

@test "the picker is taken out of Steam through gotg" {
  run remove_from_steam
  [ "$status" -eq 0 ]
  grep -qx "gotg steam remove picker" "$CALLS"
}

@test "the udev rule is removed with sudo, and only when it is there" {
  run remove_uinput_rule
  [ "$status" -eq 0 ]
  ! grep -q "sudo" "$CALLS"

  printf 'KERNEL=="uinput"\n' >"$GOTG_UDEV_PATH"
  run remove_uinput_rule
  [ "$status" -eq 0 ]
  grep -qx "sudo rm -f $GOTG_UDEV_PATH" "$CALLS"
  [ ! -e "$GOTG_UDEV_PATH" ]
}

@test "a locked SteamOS root is unlocked for the rule and locked again" {
  printf 'ID=steamos\n' >"$GOTG_OS_RELEASE"
  printf 'KERNEL=="uinput"\n' >"$GOTG_UDEV_PATH"
  steamos-readonly() { printf 'enabled\n'; }
  run remove_uinput_rule
  [ "$status" -eq 0 ]
  grep -qx "sudo steamos-readonly disable" "$CALLS"
  grep -qx "sudo steamos-readonly enable" "$CALLS"
}

@test "games and saves stay unless asked for" {
  mkdir -p "$GOTG_STATE_DIR/saves" "$GOTG_CONFIG_DIR"
  run main
  [ "$status" -eq 0 ]
  [ -d "$GOTG_STATE_DIR/saves" ]
  [[ "$output" == *"--games deletes them"* ]]

  run main --games
  [ "$status" -eq 0 ]
  [ ! -e "$GOTG_STATE_DIR" ]
  [ ! -e "$GOTG_CONFIG_DIR" ]
}

@test "a dry run changes nothing and says what it would have done" {
  NIX_HAVE="gotg gotg-ui"
  printf 'KERNEL=="uinput"\n' >"$GOTG_UDEV_PATH"
  mkdir -p "$GOTG_STATE_DIR"
  run main --dry-run --games
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run: nix profile remove gotg gotg-ui"* ]]
  [[ "$output" == *"would run: gotg steam remove picker"* ]]
  [[ "$output" == *"would run: sudo rm -f $GOTG_UDEV_PATH"* ]]
  [[ "$output" == *"would run: rm -rf $GOTG_STATE_DIR"* ]]
  [ -e "$GOTG_UDEV_PATH" ]
  [ -d "$GOTG_STATE_DIR" ]
  ! grep -q "profile remove" "$CALLS"
}

@test "Nix is left alone, and the way to remove it is said" {
  run main
  [ "$status" -eq 0 ]
  ! grep -qE "rm -rf /nix|nix-installer" "$CALLS"
  [[ "$output" == *"Nix is still installed"* ]]
  [[ "$output" == *"To remove it as well"* ]]
}

@test "the Nix removal named is the one for this install" {
  # No installer receipt and no daemon: the single-user layout comes off
  # with rm and a line out of the shell profile.
  NIX_INSTALLER_RECEIPT="$TMP/absent" NIX_DAEMON_UNIT="$TMP/absent" run nix_removal
  [[ "$output" == *"sudo rm -rf /nix"* ]]
  [[ "$output" == *"~/.profile"* ]]

  # A daemon install has no one command; the page with the six is named.
  touch "$TMP/nix-daemon.socket"
  NIX_INSTALLER_RECEIPT="$TMP/absent" NIX_DAEMON_UNIT="$TMP/nix-daemon.socket" run nix_removal
  [[ "$output" == *"uninstall#multi-user"* ]]

  # The Determinate installer leaves its own uninstaller, which wins.
  printf '#!/bin/sh\n' >"$TMP/nix-installer"
  chmod +x "$TMP/nix-installer"
  NIX_INSTALLER_RECEIPT="$TMP/nix-installer" NIX_DAEMON_UNIT="$TMP/nix-daemon.socket" run nix_removal
  [[ "$output" == *"sudo $TMP/nix-installer uninstall"* ]]
}

@test "an unknown option is refused before anything runs" {
  run main --purge
  [ "$status" -eq 1 ]
  [[ "$output" == *"unknown option: --purge"* ]]
  [ -z "$(cat "$CALLS")" ]
}
