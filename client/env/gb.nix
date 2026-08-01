# Game Boy — ares, which works out the console from the ROM header. Every
# cartridge platform gets its own file rather than sharing one, so that changing
# how Game Boy runs cannot quietly change the rest of them.
{ pkgs, ... }:
{
  emulator = pkgs.ares;
  bin = "ares";
  args = [ "{target}" ];
}
