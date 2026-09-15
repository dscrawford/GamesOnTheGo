# Tears of the Kingdom at 60 FPS, 1080p — `gotg play world.legend_of_zelda_tears_of_the_kingdom 60fps`.
# See mods/totk-ultracam.nix for what the mod is and how it is installed.
{ base, helpers, ... }:
base
// {
  title = "Tears of the Kingdom (60fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.totkUltraCam {
      fps = 60;
      width = 1920;
      height = 1080;
    }).preLaunch;
}
