# Game Boy Color — ares, which works out the console from the ROM header, with its saves
# redirected into this environment's own directory. See helpers.nix for why that
# redirect is needed and what the trailing slash is doing.
#
# No `system` yet: ares files saves under a directory named for the console, and
# that name has only been confirmed for SNES. Until someone launches a Game Boy Color
# game and reads the name off "{state}/saves/", this platform adopts nothing —
# a guess would copy old saves somewhere ares never reads, which looks exactly
# like losing them.
{ helpers, ... }:
helpers.aresPlatform {
  platform = "gbc";
  console = "GameBoyColor";
}
