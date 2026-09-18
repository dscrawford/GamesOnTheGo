# Super Smash Bros. Melee — melee-pc, the native port off doldecomp/melee,
# which finished after six years. Not Dolphin: the decompiled game code
# compiled for x86-64, running on aurora's GX/OS/PAD/DVD/CARD/THP layer,
# rendering through Dawn's WebGPU on Vulkan with SDL3 for windowing and pads.
#
# The easiest of the native ports here to wire up, because it wants exactly
# what gotg already has:
#
#   * **It reads the disc image directly**, and nod gives it RVZ along with
#     ISO, GCM and CISO — so unlike Pikmin there is no conversion, and unlike
#     Animal Crossing nothing is unpacked. The stored file is the argument.
#
#   * **The hash check is advisory.** It verifies SHA-1 against the Redump
#     DAT and says so, but upstream is explicit that unverified images still
#     play. gotg's dumps are NKit-processed and will report as unverified;
#     that is cosmetic here, where for Pikmin it was a hard stop.
#
# What the port adds over the disc: internal resolution up to 10x native,
# 4x MSAA and anisotropic filtering, a wide 16:9 combat camera with the HUD
# anchored to it, "Unlock Everything", custom soundtracks, and Dolphin-format
# .gci memory cards. Settings are on F1 with the game paused underneath.
#
# Still beta, and the part a four-player box cares about is the part that
# works: VS mode, Classic, Adventure, All-Star, Training and Stadium all run
# end to end. Online rollback play is roadmap, not feature.
{
  pkgs,
  lib,
  gotgPkgs,
  ...
}:
{
  emulator = gotgPkgs.melee-pc;
  bin = "melee";
  # Not the platform emulator: a native port. `gotg play <id> emulate`
  # is the way back to ares/dolphin when this one misbehaves.
  nativePort = true;
  # Settings are in-game on F1; there is no separate configuration program,
  # and starting one would just start the game.
  configurable = false;

  # Isolated so its preference directory — memory cards, settings, the
  # shader cache — lands under {state} rather than in the player's own
  # ~/.local/share, where a hand-installed copy of the same port may live.
  isolate = true;

  # The disc, straight from where gotg put it. See the note at the top.
  args = [ "{target}" ];

  # Memory cards and settings. The cards are Dolphin-format .gci, so they
  # are worth carrying between machines on their own merits.
  saves = [ "data/melee-pc/**" ];
  # Everything derived or bulky: the shader pipeline cache is per-GPU by
  # definition, and music and textures are the player's own files placed
  # there by hand rather than something a save should drag around.
  saveExcludes = [
    "data/melee-pc/cache/**"
    "data/melee-pc/*.db"
    "data/melee-pc/music/**"
    "data/melee-pc/textures/**"
    "data/melee-pc/logs/**"
  ];
}
