# Super Mario 64 for 4 — `gotg play usa.super_mario_64 pc-4p`.
#
# 4 copies of sm64coopdx in one frame; see mods/sm64-coop-split.nix. The
# variant is "pc-4p" because "pc" is itself a variant: this game has no
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
  players = 4;
  inherit gotgPkgs;
  base = pc;
}
