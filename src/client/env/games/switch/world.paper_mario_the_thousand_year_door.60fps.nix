# Paper Mario: The Thousand-Year Door at 60 FPS —
# `gotg play world.paper_mario_the_thousand_year_door 60fps`.
#
# The mod ships a cheat with it, switched on here, because two places in the
# game need it: hold ZL and press D-pad Down to drop back to 30 for Punie's
# escort in Chapter 2 and for the Pianta Parlor paper game, D-pad Up to return
# to 60. mods/paper-mario-ttyd.nix has the rest of the author's notes.
#
# No 120fps variant: the archive offers none, and the patch's own timing work
# is written against 60.
{ base, helpers, ... }:

base
// {
  title = "Paper Mario: The Thousand-Year Door (60fps)";
  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxModDir {
      titleId = "0100ecd018ebe000";
      name = "60fps";
      dir = helpers.paperMarioTtyd60Mod;
      enabledCheats = helpers.paperMarioTtyd60Cheats;
      # Switch: the console's own 60Hz. The patch presents the frames; raising
      # the emulated refresh rate on top would only put the game's logic back
      # where the patch just took it from.
      vsyncMode = 0;
    }).preLaunch;
}
