# Tears of the Kingdom at 120 FPS, 1440p — `gotg play world.legend_of_zelda_tears_of_the_kingdom enhanced`.
#
# The mod allows up to 240 FPS and 8K; this is the pairing a 3080 Ti has a
# chance of holding. Two things to know before choosing it over 60fps:
#
#   - It wants a 120 Hz display. Ryujinx's vsync is set to a custom 120 Hz
#     here, which on a 60 Hz panel means tearing or a wasted half.
#   - 120 is a ceiling, not a promise. The game is CPU-bound in towns and
#     GPU-bound at 1440p in the open; where it cannot hold 120, DynamicFPS
#     keeps the game speed right and the rate simply sits lower — nothing
#     runs fast or slow, only smoother or less so. Shader compilation on a
#     cold cache stutters either way.
#
# Its own environment, so its saves and settings are separate from 60fps
# and from the plain launch.
{ base, helpers, ... }:
base
// {
  title = "Tears of the Kingdom (enhanced)";
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
      width = 2560;
      height = 1440;
      shadows = 2048;
    }).preLaunch;
}
