# UltraCam for Breath of the Wild — the same mod, and the same author's
# optimizer, as the Tears of the Kingdom one beside this file.
#
# The game is locked to 30 and, like its sequel, ties physics to the frame, so
# the interesting part of the mod is not the cap but the decoupling: UltraCam's
# DynamicFPS runs the logic at its own rate and the frame rate becomes a
# ceiling the game may fall below without running slow.
#
# Two things differ from the TOTK file, and both are read off the mod's own
# PatchInfo.json rather than guessed:
#
#   * the config is one ini inside the mod (romfs/UltraCam/maxlastbreath.ini),
#     not a file on the emulated SD card. PatchInfo says "SD": false.
#   * the mod folder is named "!!!BOTW Optimizer". The exclamation marks are
#     load order — Ryujinx applies mods in name order, and this one wants to be
#     first — so the name is kept exactly as the author chose it.
#
# Only 1.6.0 is patched, which is the last version of the game.
{ pkgs, lib }:
let
  rev = "c41e68439ccd0c19bffc8a5bab08ffdf0b81b992";
  exefs = "https://raw.githubusercontent.com/MaxLastBreath/nx-optimizer/${rev}/src/PatchInfo/Breath%20Of%20The%20Wild/UltraCam/exefs";
  npdm = pkgs.fetchurl {
    url = "${exefs}/main.npdm";
    hash = "sha256-fO27V7qT46tuZgSQoKe5MnFoVABHhqKsQtOdv4uF5ng=";
  };
  subsdk = pkgs.fetchurl {
    url = "${exefs}/subsdk3";
    hash = "sha256-EhbVjLPrgCipBaPIixBKEzi0jP8rHf6sVxv/e5gm2sY=";
  };
in
{
  botwUltraCam =
    {
      fps,
      width ? 1920,
      height ? 1080,
      # The mod's own words: "Highest available resolution atm is 1152x2048,
      # anything higher and the game crashes". So the height is what is worth
      # raising here, and only that far.
      renderDistance ? 25000,
      fov ? 50,
    }:
    let
      # Upstream's own defaults, with the frame rate and the resolution taken
      # over. Written out in full rather than edited in place: the file is the
      # variant, and a mod menu that rewrote a key would otherwise leave this
      # environment quietly running something else next launch.
      ini = pkgs.writeText "maxlastbreath.ini" ''
        [Resolution]
        MaxFramerate = ${toString fps}
        MenuFPS = 60
        QualityImprovements = Off
        RenderDistance = ${toString renderDistance}
        Width = ${toString width}
        Height = ${toString height}

        [Features]
        Fov = ${toString fov}

        [UltraCam]
        TriggerWithController = On
        AutoHideUI = On
        CameraSpeed = 30.0
        Speed = 5
        AnimationSmoothing = 0.25
        AnimationFadeout = On

        [Benchmark]
        Benchmark = 0
      '';
      # The console's own 60Hz at or below 60; a custom rate above it, since
      # the emulated display is what the game's frames are handed to.
      vsync =
        if fps <= 60 then
          ".vsync_mode = 0 | .enable_custom_vsync_interval = false"
        else
          ".vsync_mode = 2 | .enable_custom_vsync_interval = true | .custom_vsync_interval = ${toString fps}";
    in
    {
      preLaunch = ''
        ryujinx="$XDG_CONFIG_HOME/Ryujinx"
        mod="$ryujinx/mods/contents/01007ef00011e000/!!!BOTW Optimizer"
        mkdir -p "$mod/exefs" "$mod/romfs/UltraCam"
        for pair in "${npdm}:main.npdm" "${subsdk}:subsdk3"; do
          src="''${pair%%:*}"
          name="''${pair##*:}"
          if ! cmp -s "$src" "$mod/exefs/$name"; then
            cp --no-preserve=mode "$src" "$mod/exefs/$name"
            echo "installed UltraCam ($name)" >&2
          fi
        done
        cp --no-preserve=mode ${ini} "$mod/romfs/UltraCam/maxlastbreath.ini"

        if [ -f "$ryujinx/Config.json" ]; then
          if ${pkgs.jq}/bin/jq '${vsync}' \
            "$ryujinx/Config.json" >"$ryujinx/Config.json.gotg"; then
            mv "$ryujinx/Config.json.gotg" "$ryujinx/Config.json"
          else
            rm -f "$ryujinx/Config.json.gotg"
          fi
        fi
      '';
    };
}
