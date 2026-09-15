# Super Mario 64 for 4 — `gotg play usa.super_mario_64 pc-4p`.
#
# 4 copies of sm64coopdx in one frame, joined to each other over a socket
# nobody has to type. See mods/sm64-coop-split.nix for how the port, the save
# directories and the controllers are kept in the same order; `pc` with no
# number is the same game for one player, which needs none of it.
#
# The variant is "pc-4p" rather than "4p" because "pc" is itself a variant:
# this game has no environment of its own to build on, so the port's is
# imported here and the split is composed over it.
{
  pkgs,
  lib,
  helpers,
  gotgPkgs,
  base,
  ...
}:
let
  pc = import ./usa.super_mario_64.pc.nix {
    inherit
      pkgs
      lib
      helpers
      gotgPkgs
      base
      ;
  };
in
pc
// (helpers.sm64CoopSplit {
  inherit gotgPkgs;
  players = 4;
  base = pc;
})
