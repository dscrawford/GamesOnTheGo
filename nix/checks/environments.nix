# Every launcher, built. The build runs shellcheck over each generated
# gotg-play, and which warnings fire depends on which placeholders an
# environment's arguments name — so ares-shaped launchers passing says nothing
# about the ports. The fullscreen block shipped exactly that hole: every
# Harkinian and decomp-port environment failed SC2034 on a variable only
# ares-shaped launchers consume, and the first build to run was a Master Quest
# launch on somebody else's machine.
#
# The flake's own environments, not a second import of src/client/env: that
# one was given neither danstick-rs nor gotg-pads, which the Four Swords
# split-screen launchers name, and failed on null before it reached them.
{ pkgs, packages }:
pkgs.linkFarm "check-environments" (
  pkgs.lib.mapAttrsToList (name: path: { inherit name path; }) (
    pkgs.lib.filterAttrs (name: _: pkgs.lib.hasPrefix "env-" name) packages
  )
)
