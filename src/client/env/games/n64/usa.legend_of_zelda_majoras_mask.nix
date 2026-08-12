# Majora's Mask — 2 Ship 2 Harkinian, the same lineage as the Ocarina of Time
# port next to it. Not emulation: a native port that extracts its assets from
# this ROM once and then runs on its own.
#
# The matching half is in data/overrides.json, which marks the entry `unzip`:
# the port reads a bare .z64, not the No-Intro archive it is distributed in, and
# where a game lands on disk has to be known without evaluating nix for
# `gotg list` to work offline.
{ helpers, pkgs, ... }:
helpers.harkinianPort {
  port = pkgs._2ship2harkinian;
  bin = "2s2h";
  appName = "2ship";
  archives = [ "mm.o2r" ];
}
