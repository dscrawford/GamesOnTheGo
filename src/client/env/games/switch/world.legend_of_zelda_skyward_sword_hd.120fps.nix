# Skyward Sword HD at 120 FPS — `gotg play world.legend_of_zelda_skyward_sword_hd 120fps`.
#
# The game already runs at 60, so there is no 60fps variant beside this one:
# the plain launch is that.
#
# Experimental, in its author's own words — see mods/skyward-sword.nix.
{ base, helpers, ... }:
base
// {
  title = "Skyward Sword HD (120fps)";

  # v1.0.1 and nothing else: the patch is written against that executable, and
  # on a 1.0.0 dump Ryujinx finds no build id it matches, applies nothing, and
  # the game runs at its usual 60 with a 120Hz display in front of it. Stated
  # here, that is a variant the client disables instead of one that looks like
  # it worked.
  gameVersionMax = "1.0.1";
  gameVersionMin = "1.0.1";

  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxModDir {
      titleId = "01002da013484000";
      name = "120fps";
      dir = helpers.skywardSword120Mod;
      # Custom, at 120Hz — the same pairing the Luigi's Mansion 120fps variant
      # uses, and for the same reason: the patch presents the frames, the
      # emulated display has to be willing to take them.
      vsyncMode = 2;
      customInterval = 120;
    }).preLaunch;
}
