# SNES — ares, which works out the console from the ROM header, with its saves
# redirected into this environment's own directory. See helpers.nix for why that
# redirect is needed and what the trailing slash is doing.
#
# The system name is the directory ares puts saves in, confirmed by launching:
# a Super Mario World save landed in "{state}/saves/Super Famicom/".
{ helpers, ... }:
helpers.aresPlatform {
  platform = "snes";
  system = "Super Famicom";
}
