# Tears of the Kingdom at 60 FPS — `gotg play world.legend_of_zelda_tears_of_the_kingdom 60fps`.
#
# The game is locked to 30 and ties its physics to the frame, so a bare
# frame-rate patch runs it at double speed. UltraCam, the mod behind NX
# Optimizer (MaxLastBreath), is what everyone uses instead: an exlaunch module
# that decouples the game's logic from the frame rate (its DynamicFPS), caps
# it at a chosen target, and takes the render resolution over from the game.
# Version 3.5.0 lists 1.4.3 among the game versions it supports.
#
# What NX Optimizer's installer does is reproduced here, read off its source:
# the module goes under the emulator's mods directory as exefs, and its
# settings are ini files on the emulated SD card at UltraCam/TOTK/Config/,
# one per section of the optimizer's Options.json, keys and values spelled
# as that file spells them. Only Main.ini is written; the rest the mod
# defaults (no cheats, no free camera unless its hotkey is pressed).
#
# 8 GiB of emulated RAM, which the optimizer sets for anything above 1080p
# and which costs nothing on a machine with plenty. Ryujinx's own vsync stays
# on at the Switch's 60 Hz: the mod paces the game, the emulator the display.
{ base, pkgs, ... }:
let
  rev = "c41e68439ccd0c19bffc8a5bab08ffdf0b81b992";
  exefs = "https://raw.githubusercontent.com/MaxLastBreath/nx-optimizer/${rev}/src/PatchInfo/Tears%20Of%20The%20Kingdom/UltraCam/exefs";
  npdm = pkgs.fetchurl {
    url = "${exefs}/main.npdm";
    hash = "sha256-1ZOoDl5T+GA6+rBlWvMbrAqjfr+KuOIFAX44tYHZoAY=";
  };
  subsdk = pkgs.fetchurl {
    url = "${exefs}/subsdk3";
    hash = "sha256-MnOh9s9a82pRNdp0ztZuKS5GVRdIB+iDq0QItzpjb7k=";
  };
  # The optimizer's own defaults for its Main tab, with the target frame
  # rate and a docked resolution the card is comfortable at.
  mainIni = pkgs.writeText "Main.ini" ''
    [FPS]
    Buffer = 8
    MaxFPS = 60.0
    MovieFPS = 30.0
    MenuFPS = 30.0
    DynamicFPS = True
    AllowLegacyPast120FPS = True
    AlwaysForceFrameLock = True
    CapDeltaTimeToFPS = True

    [Graphics]
    FXAA = True
    FSR = True
    DynamicResolution = True
    ChangeAnisotropy = False
    AnistrophyLevel = 16
    RenderDistance = 25000
    LevelOfDetailForced = False
    LevelOfDetail = 1

    [Resolution]
    AspectRatio = {16.00, 9.00}
    Docked = {1920.00, 1080.00}
    Handheld = {1280.00, 720.00}
    ForceDocked = False
    Shadows = 1024
  '';
in
base
// {
  title = "Tears of the Kingdom 60fps";
  preLaunch =
    (base.preLaunch or "")
    + ''
      ryujinx="$XDG_CONFIG_HOME/Ryujinx"
      mods="$ryujinx/mods/contents/0100f2c0115b6000/UltraCam/exefs"
      mkdir -p "$mods"
      for pair in "${npdm}:main.npdm" "${subsdk}:subsdk3"; do
        src="''${pair%%:*}"
        name="''${pair##*:}"
        if ! cmp -s "$src" "$mods/$name"; then
          cp --no-preserve=mode "$src" "$mods/$name"
          echo "installed UltraCam ($name)" >&2
        fi
      done
      config="$ryujinx/sdcard/UltraCam/TOTK/Config"
      mkdir -p "$config"
      # Written on every launch: the frame rate is the variant, not a
      # preference the mod's own menu should be able to lose.
      cp --no-preserve=mode ${mainIni} "$config/Main.ini"

      if [ -f "$ryujinx/Config.json" ]; then
        if ${pkgs.jq}/bin/jq '.dram_size = 2' "$ryujinx/Config.json" >"$ryujinx/Config.json.gotg"; then
          mv "$ryujinx/Config.json.gotg" "$ryujinx/Config.json"
        else
          rm -f "$ryujinx/Config.json.gotg"
        fi
      fi
    '';
}
