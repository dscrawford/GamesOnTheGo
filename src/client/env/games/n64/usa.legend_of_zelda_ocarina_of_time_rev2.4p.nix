# Ocarina of Time for 4 — `gotg play usa.legend_of_zelda_ocarina_of_time_rev2 4p`.
#
# 4 copies of Ship of Harkinian in one frame, meeting through Anchor's relay;
# see mods/oot-coop-split.nix. The plain launch is one player.
{
  base,
  helpers,
  gotgPkgs,
  ...
}:
base
// (helpers.harkinianCoopSplit {
  inherit gotgPkgs base;
  players = 4;
})
