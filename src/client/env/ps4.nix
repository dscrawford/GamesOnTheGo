# PS4 — shadPS4, which runs an extracted game's eboot.bin ("ps4/*" in
# data/overrides.json). Isolated: its user dir is {state}/data/shadPS4.
{ pkgs, helpers, ... }:
{
  emulator = pkgs.shadps4;
  bin = "shadps4";
  isolate = true;
  args = [
    "-g"
    "{target}"
    "{fullscreen}"
  ];
  # CLI11 takes the value in the same word, so a windowed launch passes nothing.
  fullscreenFlag = "--fullscreen=true";

  # A package is unpacked once, on install; the tree is what is kept.
  recipes = {
    single_file = [
      helpers.steps.pickBase
      helpers.steps.unpackPs4Pkg
      helpers.steps.placeTree
    ];
  };

  # Some games need the console's own system modules (libSceNgs2.sprx and the
  # like), which are not ours to ship. Optional: shadPS4 stubs most of them.
  firmware = {
    into = "data/shadPS4/sys_modules";
    file = "sys_modules.zip";
    flat = "*.sprx";
  };

  # Its settings screen is a separate launcher; with no game it only says so.
  configurable = false;

  saves = [ "data/shadPS4/home/*/savedata/**" ];
}
