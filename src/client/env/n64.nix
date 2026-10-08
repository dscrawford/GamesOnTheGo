# Nintendo 64 — ares, which works out the console from the ROM header, with its saves
# redirected into this environment's own directory. See helpers.nix for why that
# redirect is needed and what the trailing slash is doing.
#
# No `system` yet: ares files saves under a directory named for the console, and
# nobody has read this one's name off "{state}/saves/" after a launch. Until
# then this platform adopts nothing -- a guess would copy old saves somewhere
# ares never reads, which looks exactly like losing them.
{ helpers, ... }:
helpers.aresPlatform {
  platform = "n64";
  console = "Nintendo64";
  # PaperBoat's HD pack is the first mod to travel as an extra: a .o2r beside
  # the ROM, which the game's launcher links in (gotg_extra).
  recipe = helpers.bundleRecipe [ "*.o2r" ];
}
