# Tears of the Kingdom at 120 FPS, 1080p — `gotg play world.legend_of_zelda_tears_of_the_kingdom 120fps`.
#
# The frame rate of `enhanced` without its resolution: that variant pairs 120
# with 1440p and larger shadows, which is a GPU asking price this one does not
# make. On a handheld or a 1080p panel this is the one to try first.
{ base, helpers, ... }:
base
// {
  title = "Tears of the Kingdom (120fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.totkUltraCam {
      fps = 120;
      width = 1920;
      height = 1080;
    }).preLaunch;
}
