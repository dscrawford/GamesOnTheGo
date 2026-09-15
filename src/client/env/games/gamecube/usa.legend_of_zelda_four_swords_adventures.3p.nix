# Four Swords Adventures for 3 — `gotg play usa.legend_of_zelda_four_swords_adventures 3p`.
#
# 3 Game Boy Advances, one per player, in one window with the game. See
# mods/four-swords-split.nix for how the windows, the ports and the controllers
# are kept in the same order; the plain launch (no variant) is the one-player
# game on a GameCube controller, which needs none of this.
{
  base,
  helpers,
  gotgPkgs,
  ...
}:
base
// (helpers.fourSwordsSplit {
  inherit gotgPkgs base;
  players = 3;
})
