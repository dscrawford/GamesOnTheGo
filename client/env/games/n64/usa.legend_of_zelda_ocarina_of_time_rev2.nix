# Ocarina of Time — Ship of Harkinian. Not emulation: a native port built from
# the decompilation, which reads this ROM once and runs on its own afterwards.
#
# The catalog entry is NTSC 1.2 (US), one of the dumps SoH supports; the list is
# docs/supportedHashes.json in the Shipwright repo, and a ROM outside it stops
# at "is not a ROM or does not match supported ROMs" during extraction.
#
# The matching half is in data/overrides.json, which marks the entry `unzip`:
# the port reads a bare .z64, not the No-Intro archive it is distributed in.
{ helpers, pkgs, ... }:
helpers.harkinianPort {
  port = pkgs.shipwright;
  bin = "soh";
  appName = "soh";
  archives = [
    "oot.o2r"
    "oot-mq.o2r"
  ];
}
