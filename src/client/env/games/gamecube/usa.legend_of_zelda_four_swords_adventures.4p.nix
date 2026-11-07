# Four Swords Adventures for 4 — `gotg play usa.legend_of_zelda_four_swords_adventures 4p`.
#
# 4 Game Boy Advances, one per player, in one window with the game; see
# mods/four-swords-split.nix. The plain launch is the one-player GameCube game.
{
  base,
  helpers,
  gotgPkgs,
  ...
}:
helpers.coopVariant {
  split = helpers.fourSwordsSplit;
  players = 4;
  inherit base gotgPkgs;
}
// {
  # Stated, because coopVariant starts from `base` and would carry its facet.
  windows.reason = "a split-screen session of Dolphin and 4 GBA windows inside danstick's sandbox, which is Linux only";
}
