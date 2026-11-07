# Wii — the same dolphin as the GameCube, kept separate so Wii-only settings
# have somewhere to go.
{ helpers, gotgPkgs, ... }:
helpers.dolphinPlatform { windowsEmulator = gotgPkgs.windows.dolphin; }
