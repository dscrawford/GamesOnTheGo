# PlayStation — ares, saves redirected as helpers.nix explains. No `system`
# yet: nobody has read the memory card's save directory off a real launch.
{ helpers, ... }:
let
  base = helpers.aresPlatform {
    platform = "psx";
    aresSystem = "PlayStation";
    console = "PlayStation";
    # A Redump disc is a .cue and the tracks it names, one 7z per disc. A .chd
    # arrives as a single file and needs no recipe.
    recipe.single_archive = [
      helpers.steps.extract7z
      helpers.steps.placeCueTree
    ];
  };
in
base
// {
  # ares will not boot a disc without the console's BIOS, and picks the one
  # for the disc's region (playstation.cpp). Like the GBA's, it is placed by
  # hand on the server as /Games/psx/<file> and fetched on demand.
  keys = {
    into = "bios";
    files = [
      "bios-us.bin"
      "bios-japan.bin"
      "bios-europe.bin"
    ];
  };

  # <EmulatorName>/Firmware/<Type>.<Region>, as settings.cpp binds them.
  args = base.args ++ [
    "--setting"
    "PlayStation/Firmware/BIOS.US={state}/bios/bios-us.bin"
    "--setting"
    "PlayStation/Firmware/BIOS.Japan={state}/bios/bios-japan.bin"
    "--setting"
    "PlayStation/Firmware/BIOS.Europe={state}/bios/bios-europe.bin"
  ];
}
