# Ocarina of Time for 3 — `gotg play usa.legend_of_zelda_ocarina_of_time_rev2 3p`.
#
# 3 copies of Ship of Harkinian in one frame, meeting through Anchor's relay;
# see mods/oot-coop-split.nix. The plain launch is one player.
{
  base,
  helpers,
  gotgPkgs,
  ...
}:
helpers.coopVariant {
  split = helpers.harkinianCoopSplit;
  players = 3;
  inherit base gotgPkgs;
}
