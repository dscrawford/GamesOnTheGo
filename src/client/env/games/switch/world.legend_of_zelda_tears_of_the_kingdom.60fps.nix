# Tears of the Kingdom at 60 FPS, 1080p — `gotg play world.legend_of_zelda_tears_of_the_kingdom 60fps`.
# See mods/totk-ultracam.nix for what the mod is and how it is installed.
{ base, helpers, ... }:
base
// {
  title = "Tears of the Kingdom (60fps)";
# UltraCam is machine code written against one executable, and its newest build
# reaches 1.4.2 — its own README lists every version it supports and 1.4.3 is
# not among them (nx-optimizer issue #300, PR #330: the bundled subsdk3 "lacked
# the required hooks", crashing with a read at 0x0). So this says what it can
# take, and the client runs the newest update at or below it rather than
# whatever is newest.
  gameVersionMax = "1.4.2";

  preLaunch =
    (base.preLaunch or "")
    + (helpers.totkUltraCam {
      fps = 60;
      width = 1920;
      height = 1080;
    }).preLaunch;
}
