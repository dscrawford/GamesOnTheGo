# Majora's Mask — not emulation at all. 2s2h is a native PC port that extracts
# its assets from a bare .z64 once and then runs on its own, so it replaces ares
# outright and takes no ROM argument.
#
# The matching half of this lives in data/overrides.json, which marks the entry
# `unzip`: the port cannot read the No-Intro zip, and where a game lands on disk
# has to be known without evaluating nix, for `gotg list` to work offline.
{ pkgs, ... }:
{
  emulator = pkgs._2ship2harkinian;
  bin = "2s2h";
  args = [ ];
}
