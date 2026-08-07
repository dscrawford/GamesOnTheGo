# Luigi's Mansion 2 HD at 60 FPS — `gotg play world.banra 60fps`.
#
# The game ships locked to 30. There are two ways to raise that and only one of
# them is right.
#
# The wrong way is Ryujinx's VSync toggle (F1), which raises the *emulated
# refresh rate*. Ryujinx says what that costs in its own settings text: "In some
# titles, this may speed up or slow down the rate of gameplay logic ... no
# guarantees for how gameplay will be affected." In this game that is the bug
# where Luigi crawls up stairs — game logic running off a refresh rate it was
# never written for.
#
# The right way is the patch. It presents one frame per vblank, so with VSync
# left on "Switch" — 60Hz — that is 60 FPS with the logic untouched, and nothing
# to toggle at launch.
#
# The `120fps` variant beside this one pairs the same patch with a 120Hz
# emulated refresh rate, which is the mod author's stated ceiling and does lean
# on the mechanism described above.
#
# NOTE ON THE NAME: `world.banra` is the scene release's internal codename, not
# the game's. It came in that way and the id is the catalog's, so this file is
# named to match. When the entry is renamed, this renames with it.
{ base, helpers, ... }:

base
// {
  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxMod {
      titleId = "010048701995e000";
      name = "60fps";
      patch = helpers.luigisMansion2FpsPatch;
      exe = "${base.emulator}/bin/${base.bin}";
      # Switch: the console's own 60Hz. The patch does the work; anything else
      # here would only reintroduce what it fixes.
      vsyncMode = 0;
    }).preLaunch;
}
