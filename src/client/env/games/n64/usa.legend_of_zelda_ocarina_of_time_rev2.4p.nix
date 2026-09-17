# Ocarina of Time for 4 — `gotg play usa.legend_of_zelda_ocarina_of_time_rev2 4p`.
#
# 4 copies of Ship of Harkinian in one frame, meeting through Anchor's relay
# run here. See mods/oot-coop-split.nix for how the relay, the settings and
# the controllers are kept in the same order; the plain launch is one player
# and needs none of it.
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
