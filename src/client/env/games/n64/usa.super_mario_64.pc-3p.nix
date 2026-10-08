# Super Mario 64 for 3 — `gotg play usa.super_mario_64 pc-3p`.
#
# 3 copies of sm64coopdx in one frame; see mods/sm64-coop-split.nix. The
# variant is "pc-3p" because "pc" is itself a variant: this game has no
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
helpers.coopVariant {
  split = helpers.sm64CoopSplit;
  players = 3;
  inherit gotgPkgs;
  base = pc;
}
