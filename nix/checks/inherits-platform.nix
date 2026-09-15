# A variant's launcher still does what its platform's does.
#
# `base // patch` replaces, so a game file that writes `preLaunch = ...` drops
# the platform's entirely — and the platform's is where Dolphin is told to read
# a pad it does not have focus on, which backend to draw with, and not to stop
# on a modal dialog. The Four Swords split-screen variants shipped exactly that
# bug: the launch worked, the game ran, and an NKit warning nobody could click
# sat in front of it.
#
# Grepped from the built launchers rather than asserted about the nix, because
# what went missing is text in a script, and that is what can be looked for.
{ pkgs }:
let
  envs = import ../../src/client/env { inherit pkgs; };
  lib = pkgs.lib;

  # Every environment of a platform, the platform's own first: a variant's name
  # is the platform's with more on the end.
  familyOf =
    platform:
    lib.filterAttrs (name: _: name == "env-${platform}" || lib.hasPrefix "env-${platform}-" name) envs;

  # One line each from the platform's preLaunch that a variant must not lose.
  # Not the whole block: a variant is allowed to add to it, and one line per
  # decision is enough to catch a launcher that dropped the lot.
  mustCarry = {
    gamecube = [
      "BackgroundInput"
      "SkipNKitWarning"
    ];
    wiiu = [ ];
    switch = [
      # Ryujinx writes its own default config on a first run, and every frame
      # rate variant edits that config — so a variant that replaced this rather
      # than adding to it would have nothing to edit on a fresh machine.
      "gotg_ryujinx_config"
      # A service Ryujinx has not implemented answers instead of throwing:
      # without it Tears of the Kingdom dies on its first save.
      "ignore_missing_services"
    ];
  };

  checkOne =
    platform: needles:
    lib.mapAttrsToList (name: env: ''
      launcher="${env}/bin/gotg-play"
      ${lib.concatMapStringsSep "\n" (needle: ''
        grep -qF ${lib.escapeShellArg needle} "$launcher" ||
          { echo "${name} lost ${needle} — a game file replaced the platform's preLaunch instead of adding to it" >&2
            exit 1; }
      '') needles}
    '') (familyOf platform);
in
pkgs.runCommand "check-inherits-platform" { } ''
  ${lib.concatStringsSep "\n" (lib.flatten (lib.mapAttrsToList checkOne mustCarry))}
  touch $out
''
