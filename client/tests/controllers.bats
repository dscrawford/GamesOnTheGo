#!/usr/bin/env bats
# `gotg controllers install-rules` — the privileged step, and the machines where
# it is the wrong answer.

bats_require_minimum_version 1.5.0

load helper

setup() {
  setup_env

  # A stand-in rules package, and a nix that hands back where it lives.
  export FAKE_STORE="$TEST_TMP/store/steam-devices-udev-rules"
  mkdir -p "$FAKE_STORE/lib/udev/rules.d"
  printf '# rules\n' >"$FAKE_STORE/lib/udev/rules.d/60-steam-input.rules"
  printf '# vr\n' >"$FAKE_STORE/lib/udev/rules.d/60-steam-vr.rules"

  export GOTG_FLAKE="$TEST_TMP/flake"
  mkdir -p "$GOTG_FLAKE" "$TEST_TMP/bin"
  : >"$GOTG_FLAKE/flake.nix"
  export GOTG_NIX="$TEST_TMP/bin/nix"
  {
    printf '#!%s\n' "$(command -v bash)"
    printf 'printf "%%s\\n" "$FAKE_STORE"\n'
  } >"$GOTG_NIX"
  chmod +x "$GOTG_NIX"

  # Not NixOS, and a rules directory that is not the real one.
  export GOTG_UDEV_RULES_DIR="$TEST_TMP/udev-rules.d"
  export GOTG_NIXOS_MARKER="$TEST_TMP/not-nixos"
  mkdir -p "$GOTG_UDEV_RULES_DIR"
}

@test "without --apply it prints the commands and touches nothing" {
  gotg controllers install-rules
  [ "$status" -eq 0 ]

  # The commands go to stdout, so they can be read or piped; the prose does not.
  [[ "$output" == *"ln -sfn $FAKE_STORE/lib/udev/rules.d/60-steam-input.rules"* ]]
  [[ "$output" == *"udevadm control --reload"* ]]
  [[ "$stderr" == *"need root"* ]]

  # Nothing was created, and no sudo was run on the quiet.
  [ -z "$(ls -A "$GOTG_UDEV_RULES_DIR")" ]
}

@test "only the input rules, not the VR ones" {
  gotg controllers install-rules
  [ "$status" -eq 0 ]
  [[ "$output" == *"60-steam-input.rules"* ]]
  # Nothing here is about a headset.
  [[ "$output" != *"60-steam-vr.rules"* ]]
}

@test "a link that already points at the current rules is left alone" {
  ln -s "$FAKE_STORE/lib/udev/rules.d/60-steam-input.rules" \
    "$GOTG_UDEV_RULES_DIR/60-steam-input.rules"

  gotg controllers install-rules
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"already points at the current rules"* ]]
  [[ "$stderr" == *"nothing to do"* ]]
  # No command offered, because there is nothing to run.
  [[ "$output" != *"ln -sfn"* ]]
}

@test "a link pointing at an older store path is offered for replacement" {
  # What a flake update looks like from here: the symlink still resolves, but
  # not to the rules this build would use.
  mkdir -p "$TEST_TMP/old-store/lib/udev/rules.d"
  printf '# old\n' >"$TEST_TMP/old-store/lib/udev/rules.d/60-steam-input.rules"
  ln -s "$TEST_TMP/old-store/lib/udev/rules.d/60-steam-input.rules" \
    "$GOTG_UDEV_RULES_DIR/60-steam-input.rules"

  gotg controllers install-rules
  [ "$status" -eq 0 ]
  [[ "$output" == *"ln -sfn $FAKE_STORE"* ]]
}

@test "on NixOS it refuses and points at the module" {
  : >"$GOTG_NIXOS_MARKER"

  gotg controllers install-rules --apply
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"NixOS"* ]]
  [[ "$stderr" == *"nixosModules.controllers"* ]]
  [[ "$stderr" == *"programs.gotg.controllers.enable"* ]]
  # And it refuses before doing anything, --apply or not.
  [ -z "$(ls -A "$GOTG_UDEV_RULES_DIR")" ]
}

@test "an unknown option is refused rather than ignored" {
  gotg controllers install-rules --force
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"--apply"* ]]
}

@test "with no flake it says where to point one" {
  export GOTG_FLAKE="$TEST_TMP/nowhere"
  gotg controllers install-rules
  [ "$status" -ne 0 ]
  [[ "$stderr" == *"no flake at"* ]]
}
