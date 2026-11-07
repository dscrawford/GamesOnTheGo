# GameCube — dolphin, which takes the disc image with -e ("exec").
{ helpers, gotgPkgs, ... }:
helpers.dolphinPlatform { windowsEmulator = gotgPkgs.windows.dolphin; }
