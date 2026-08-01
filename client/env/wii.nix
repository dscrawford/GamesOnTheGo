# Wii — the same dolphin as the GameCube, kept separate so Wii-only settings
# have somewhere to go.
{ pkgs, ... }:
{
  emulator = pkgs.dolphin-emu;
  bin = "dolphin-emu";
  args = [
    "-e"
    "{target}"
  ];
}
