# Breath of the Wild at 120 FPS, 1080p — `gotg play world.legend_of_zelda_breath_of_the_wild 120fps`.
#
# Two things to know before choosing this over 60fps:
#
#   - It wants a 120 Hz display. Ryujinx's emulated refresh rate is set to a
#     custom 120 here, which on a 60 Hz panel means tearing or a wasted half.
#   - 120 is a ceiling rather than a promise. Breath of the Wild is CPU-bound
#     in towns and in weather, and where it cannot hold 120 the mod's dynamic
#     rate keeps the game speed right and the frame rate simply sits lower.
#
# The resolution stays 1080p on purpose: the mod's own notes cap this game at
# 1152x2048 before it crashes, and the frame rate is what this variant is for.
{ base, helpers, ... }:
base
// {
  title = "Breath of the Wild (120fps)";

  # UltraCam patches 1.6.0 and nothing else — the last version of the game,
  # and the only one its exefs was built against. Both ends stated, because
  # "only 1.6.0" is a window a version below fails as surely as one above:
  # on an older dump the mod loads, hooks nothing, and the game is simply
  # itself until it is not.
  gameVersionMax = "1.6.0";
  gameVersionMin = "1.6.0";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.botwUltraCam {
      fps = 120;
      width = 1920;
      height = 1080;
    }).preLaunch;
}
