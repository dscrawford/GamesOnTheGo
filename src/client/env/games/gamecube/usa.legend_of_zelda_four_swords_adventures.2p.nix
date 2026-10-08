# Four Swords Adventures for 2 — `gotg play usa.legend_of_zelda_four_swords_adventures 2p`.
#
# 2 Game Boy Advances, one per player, in one window with the game; see
# mods/four-swords-split.nix. The plain launch is the one-player GameCube game.
{
  base,
  helpers,
  gotgPkgs,
  ...
}:
base
// (helpers.fourSwordsSplit {
  inherit gotgPkgs base;
  players = 2;
})
