# Game Boy Advance — ares, which works out the console from the ROM header, with its saves
# redirected into this environment's own directory. See helpers.nix for why that
# redirect is needed and what the trailing slash is doing.
#
# No `system` yet: ares files saves under a directory named for the console, and
# that name has only been confirmed for SNES. Until someone launches a Game Boy Advance
# game and reads the name off "{state}/saves/", this platform adopts nothing —
# a guess would copy old saves somewhere ares never reads, which looks exactly
# like losing them.
{ helpers, ... }:
let
  base = helpers.aresPlatform {
    platform = "gba";
    console = "GameBoyAdvance";
  };
in
base
// {
  # ares refuses to start a GBA game without the console's BIOS — its loader
  # reads the file and errors out before the game if it is absent or wrong.
  # Like the Switch keys it is a console's own software, placed by hand on the
  # server and fetched here on demand, never in the store. ares opens the zip
  # itself (Emulator::loadFirmware special-cases .zip), so it travels as
  # /Games/gba/bios.zip and is handed over unopened.
  keys = {
    into = "bios";
    files = [ "bios.zip" ];
  };

  # The settings key is <EmulatorName>/Firmware/<Type>.<Region>, read off
  # settings.cpp — ares restores command-line overrides before it saves, so
  # this never rewrites a path someone set by hand in its own UI.
  args = base.args ++ [
    "--setting"
    "GameBoyAdvance/Firmware/BIOS.World={state}/bios/bios.zip"
  ];
}
