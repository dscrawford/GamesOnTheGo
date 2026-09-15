# Super Mario RPG at 120 FPS — `gotg play world.super_mario_rpg 120fps`.
#
# The remake already runs at 60, so there is no 60fps variant beside this one:
# the plain launch is that.
#
# Work in progress, by the archive's own label — see mods/super-mario-rpg.nix.
{ base, helpers, ... }:

base
// {
  title = "Super Mario RPG (120fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxModDir {
      titleId = "0100bc0018138000";
      name = "120fps";
      dir = helpers.superMarioRpg120Mod;
      # Custom, at 120Hz — the same pairing the Luigi's Mansion and Skyward
      # Sword 120fps variants use, and for the same reason: the patch presents
      # the frames, the emulated display has to be willing to take them.
      vsyncMode = 2;
      customInterval = 120;
    }).preLaunch;
}
