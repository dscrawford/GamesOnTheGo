# Super Mario 64 for 2 — `gotg play usa.super_mario_64 pc-2p`.
#
# 2 copies of sm64coopdx in one frame; see mods/sm64-coop-split.nix. The
# variant is "pc-2p" because "pc" is itself a variant: this game has no
# environment of its own, so the port's is imported and the split composed over it.
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
  players = 2;
  base = pc;
})
