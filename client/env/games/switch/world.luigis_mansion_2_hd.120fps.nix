# Luigi's Mansion 2 HD at 120 FPS — `gotg play world.luigis_mansion_2_hd 120fps`.
#
# The same patch as the `60fps` variant, which presents one frame per vblank —
# so the frame rate is whatever the emulated refresh rate is, and this sets that
# to 120Hz.
#
# THIS LEANS ON THE THING THAT CAUSES THE STAIRS BUG, and it is worth being
# plain about that. Ryujinx calls a custom refresh rate experimental and warns
# it "may speed up or slow down the rate of gameplay logic"; in this game that
# shows up as Luigi crawling up stairs. 120 is the mod author's recommended
# ceiling — "capping your FPS to 120 or lower is recommended" — but that is a
# recommendation from testing, not a guarantee, and the failure is gradual
# rather than obvious.
#
# So: play the stairs in the mansion's entrance hall before settling on this. If
# anything drags, `60fps` is the variant that does not touch the refresh rate at
# all.
#
# The two keep separate settings and separate saves, being separate
# environments — switching between them does not carry progress across.
{ base, helpers, ... }:

base
// {
  preLaunch =
    (base.preLaunch or "")
    + (helpers.ryujinxMod {
      titleId = "010048701995e000";
      name = "120fps";
      patch = helpers.luigisMansion2FpsPatch;
      # Custom, at 120Hz. Mode 2 and the interval's units — a refresh rate in
      # Hz, per Ryujinx's own description — were both read off the emulator
      # rather than assumed.
      vsyncMode = 2;
      customInterval = 120;
    }).preLaunch;
}
