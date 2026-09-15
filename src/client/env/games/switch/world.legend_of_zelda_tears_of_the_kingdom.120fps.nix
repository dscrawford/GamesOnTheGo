# Tears of the Kingdom at 120 FPS, 1080p — `gotg play world.legend_of_zelda_tears_of_the_kingdom 120fps`.
#
# The frame rate of `enhanced` without its resolution: that variant pairs 120
# with 1440p and larger shadows, which is a GPU asking price this one does not
# make. On a handheld or a 1080p panel this is the one to try first.
{ base, helpers, ... }:
base
// {
  title = "Tears of the Kingdom (120fps)";
# UltraCam is machine code written against one executable, and its newest build
# reaches 1.4.2 — its own README lists every version it supports and 1.4.3 is
# not among them (nx-optimizer issue #300, PR #330: the bundled subsdk3 "lacked
# the required hooks", crashing with a read at 0x0). So this says what it can
# take, and the client runs the newest update at or below it rather than
# whatever is newest.
  gameVersionMax = "1.4.2";
  # And no lower than 1.1.0: the same subsdk3 that lacks 1.4.3's hooks was
  # never given 1.0.0's either, so the launch-day dump fails the same way.
  gameVersionMin = "1.1.0";

  preLaunch =
    (base.preLaunch or "")
    + (helpers.totkUltraCam {
      fps = 120;
      width = 1920;
      height = 1080;
    }).preLaunch;
}
