# Kirby and the Forgotten Land at 60 FPS —
# `gotg play world.kirby_and_the_forgotten_land 60fps`.
#
# The game ships locked to 30 and this is the only frame rate above it on offer:
# the patch is the *static* one, so the game's logic still expects a fixed rate
# and a faster emulated display would speed the game up rather than smooth it
# out. See mods/kirby-forgotten-land.nix — there is no 120fps variant for the
# same reason, and one appearing here would be a bug, not an upgrade.
{ base, helpers, ... }:

base
// {
  title = "Kirby and the Forgotten Land (60fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxModDir {
      titleId = "01004d300c5ae000";
      name = "60fps";
      dir = helpers.kirby60Mod;
      # Switch: the console's own 60Hz, which is the ceiling the static patch
      # was written against.
      vsyncMode = 0;
    }).preLaunch;
}
