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
#
# Three more of the author's patches ride along, because 60 frames of a
# smeared picture is not the win it sounds like:
#
#   * **1920x1080 in-engine.** The remake renders at its own internal size and
#     the emulator's resolution slider is the wrong tool -- every settings
#     guide for this game leaves that at 1x and raises the resolution inside
#     the engine instead. 1080p is the tier the author ships without a
#     caveat; above it there is a real lighting fault, worst in shops and on
#     tall sprites, and a separate Lighting Fix V3 folder exists for anyone
#     who wants 1440p or 4K badly enough to carry it.
#
#   * **The sharpening filter off.** It is a post-process the game applies
#     itself, and at the internal resolution it was tuned for it reads as
#     ringing along every edge.
#
#   * **Lighting Fix V3.** The author files it under "for 1440p and above",
#     and at 1080p it still earns its place: in dialogue, where the game draws
#     its own cinematic bars, the top bar filled with bright specks -- coin
#     yellow and white, fragments of the scene. That is an effects buffer the
#     resolution patch did not resize, glow landing at the wrong offset and
#     wrapping to the top, which is the class of fault this fix's three
#     patched words and three replaced light files are for. None of its
#     addresses overlap the other three patches; checked, not assumed.
#
#   * **8GiB of emulated memory.** Not a preference: with the resolution
#     patch and the console's own 4GiB the game dies about ten seconds in --
#     "Invalid memory access at virtual address 0x0", then abort. Measured
#     here, both ways round.
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
    }).preLaunch
    + (helpers.ryujinxDram 2).preLaunch
    + (helpers.ryujinxModOnly {
      titleId = "0100ecd018ebe000";
      name = "1080p";
      dir = helpers.paperMarioTtydMod "1920x1080 v1.0.1";
    }).preLaunch
    + (helpers.ryujinxModOnly {
      titleId = "0100ecd018ebe000";
      name = "no-sharpening";
      dir = helpers.paperMarioTtydMod "Disabled Sharpening Filter v1.0.1";
    }).preLaunch
    + (helpers.ryujinxModOnly {
      titleId = "0100ecd018ebe000";
      name = "lighting-fix";
      dir = helpers.paperMarioTtydMod "Lighting Fix V3 1.0.1";
    }).preLaunch;
}
