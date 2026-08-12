# Ocarina of Time: Master Quest — the same Ship of Harkinian as the retail game.
#
# The extractor recognises a Master Quest ROM on its own and writes oot-mq.o2r
# where a retail one produces oot.o2r, so nothing here has to say which it is.
# Master Quest keeps its own environment rather than sharing one with the retail
# game: SoH can hold both archives at once, but two environments means two sets
# of saves and settings, and either game can be removed on its own. The port
# itself is built once and shared.
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
