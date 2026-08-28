# Luigi's Mansion 2 HD's frame-rate patch. A mod is not an emulator shape — it
# changes a single game's own files — so it lives beside the other mods rather
# than with the launchers.
{ pkgs }:
{
  # Luigi's Mansion 2 HD's frame-rate patch, shared by the variants that differ
  # only in the emulated refresh rate they pair it with.
  #
  # Pinned to a commit rather than a branch on purpose: it is two lines of
  # machine code written against one build of one game, so a silent change
  # upstream would be a silent change to the game's timing.
  #
  # It writes `mov r1, #1` over the swap interval — present one frame per
  # vblank. The sibling "FPS Unlocked" patch writes #0 instead, removing the cap
  # entirely, which is how people overshoot into the stairs bug; it is not used.
  luigisMansion2FpsPatch = pkgs.fetchurl {
    url =
      "https://raw.githubusercontent.com/StevensND/switch-port-mods/"
      + "774e7f7c85561ddd865c1cab555f2a335054d522/"
      + "Luigi%27s%20Mansion%202%20HD/%5B010048701995E000%5D/60FPS/1.0.0.pchtxt";
    hash = "sha256-CFxkNqG9m+FvzMK0/ECw6qK4HC8hzuJu+Qee3OwIMLg=";
  };

}
