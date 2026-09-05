# UltraCam for Tears of the Kingdom — the module behind NX Optimizer
# (MaxLastBreath), version 3.5.0, which lists 1.4.3 among the game versions
# it supports.
#
# The game is locked to 30 and ties its physics to the frame, so a bare
# frame-rate patch runs it at double speed. UltraCam is an exlaunch module
# that decouples the game's logic from the frame rate (its DynamicFPS), caps
# it at a chosen target, and takes the render resolution over from the game.
#
# What the optimizer's installer does is reproduced here, read off its
# source: the module goes under the emulator's mods directory as exefs, and
# its settings are ini files on the emulated SD card at UltraCam/TOTK/Config/,
# one per section of the optimizer's Options.json, keys and values spelled as
# that file spells them. Only Main.ini is written; the rest the mod defaults
# (no cheats, no free camera unless its hotkey is pressed).
{ pkgs, lib }:
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
  # The optimizer writes its dropdown values as "{W.00, H.00}".
  pair = a: b: "{${toString a}.00, ${toString b}.00}";
in
{
  # A preLaunch for one frame-rate and resolution. fps is the cap; the
  # emulator's own vsync is set beside it so the display keeps up — the
  # Switch's 60 Hz mode below 60, a custom rate above it.
  totkUltraCam =
    {
      fps,
      width,
      height,
      shadows ? 1024,
      # 0 = 4 GiB, 1 = 6, 2 = 8: the optimizer's own choice above 1080p.
      dram ? 2,
    }:
    let
      mainIni = pkgs.writeText "Main.ini" ''
        [FPS]
        Buffer = 8
        MaxFPS = ${toString fps}.0
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
        AspectRatio = ${pair 16 9}
        Docked = ${pair width height}
        Handheld = ${pair 1280 720}
        ForceDocked = False
        Shadows = ${toString shadows}
      '';
      vsync =
        if fps <= 60 then
          ".vsync_mode = 0 | .enable_custom_vsync_interval = false"
        else
          ".vsync_mode = 2 | .enable_custom_vsync_interval = true | .custom_vsync_interval = ${toString fps}";
    in
    {
      preLaunch = ''
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
          if ${pkgs.jq}/bin/jq '.dram_size = ${toString dram} | ${vsync}' \
            "$ryujinx/Config.json" >"$ryujinx/Config.json.gotg"; then
            mv "$ryujinx/Config.json.gotg" "$ryujinx/Config.json"
          else
            rm -f "$ryujinx/Config.json.gotg"
          fi
        fi
      '';
    };
}
