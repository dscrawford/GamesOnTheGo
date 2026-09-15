# Breath of the Wild at 60 FPS, 1080p — `gotg play world.legend_of_zelda_breath_of_the_wild 60fps`.
#
# The same mod and the same author as the Tears of the Kingdom variants, which
# is not a coincidence: the two games share an engine, and both tie physics to
# the frame. See mods/botw-ultracam.nix for what is installed and where.
#
# Its own environment, so its saves and settings are separate from the plain
# launch and from 120fps.
{ base, helpers, ... }:
base
// {
  title = "Breath of the Wild (60fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.botwUltraCam {
      fps = 60;
      width = 1920;
      height = 1080;
    }).preLaunch;
}
