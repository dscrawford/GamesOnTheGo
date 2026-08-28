# Every launcher, built. The build runs shellcheck over each generated
# gotg-play, and which warnings fire depends on which placeholders an
# environment's arguments name — so ares-shaped launchers passing says nothing
# about the ports. The fullscreen block shipped exactly that hole: every
# Harkinian and decomp-port environment failed SC2034 on a variable only
# ares-shaped launchers consume, and the first build to run was a Master Quest
# launch on somebody else's machine.
{ pkgs }:
let
  envs = import ../../src/client/env { inherit pkgs; };
in
pkgs.linkFarm "check-environments" (
  pkgs.lib.mapAttrsToList (name: path: { inherit name path; }) envs
)
