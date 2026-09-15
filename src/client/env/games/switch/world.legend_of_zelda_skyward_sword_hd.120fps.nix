# Skyward Sword HD at 120 FPS — `gotg play world.legend_of_zelda_skyward_sword_hd 120fps`.
#
# The game already runs at 60, so there is no 60fps variant beside this one:
# the plain launch is that.
#
# Experimental, in its author's own words — see mods/skyward-sword.nix. It also
# only patches v1.0.1; on a 1.0.0 dump Ryujinx finds no build id it matches and
# the game runs at its usual 60, which is what "this variant did nothing" looks
# like.
{ base, helpers, ... }:
base
// {
  title = "Skyward Sword HD (120fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxMod {
      titleId = "01002da013484000";
      name = "120fps";
      patch = helpers.skywardSword120Patch;
      gameVersion = "1.0.1";
      # Custom, at 120Hz — the same pairing the Luigi's Mansion 120fps variant
      # uses, and for the same reason: the patch presents the frames, the
      # emulated display has to be willing to take them.
      vsyncMode = 2;
      customInterval = 120;
    }).preLaunch;
}
