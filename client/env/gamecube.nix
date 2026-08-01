# GameCube — dolphin, which takes the disc image with -e ("exec").
{ pkgs, ... }:
{
  emulator = pkgs.dolphin-emu;
  bin = "dolphin-emu";
  args = [
    "-e"
    "{target}"
  ];
}
