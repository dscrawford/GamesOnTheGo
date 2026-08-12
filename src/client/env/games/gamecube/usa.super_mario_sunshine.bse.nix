# Super Mario Sunshine with BetterSunshineEngine — `gotg play usa.super_mario_sunshine bse`.
#
# BSE is a framework rather than a mod with a front end: it loads under Kuribo,
# extends the game's engine and hands an API to modules built on it. So a
# correct install looks like ordinary Super Mario Sunshine on the title screen,
# and the way to tell it apart from a failed one is Kuribo's own log line —
# boot with OSREPORT logging on and it names each module as it loads it. The
# `bsmso` variant beside this file is what BSE existing is for.
#
# NOT INSTALLED: the six surfing parameters. The README asks for them to be
# written *inside* the game's params.szs, which is a Yaz0 RARC archive. wszst
# can read that container but not write one, and nothing packaged can — so the
# engine installs in full while the surfing mechanic keeps the game's stock
# values. When a RARC writer exists, that step goes in the `install` below.
{
  base,
  helpers,
  gotgPkgs,
  ...
}:

let
  bse = helpers.betterSunshineEngine;
  disc = helpers.kuriboSunshineDisc {
    inherit gotgPkgs;
    cache = "bse";
    # Verbatim from the release README: the Kuribo! folder into ./files/, and
    # main.dol and boot.bin into ./sys/.
    install = ''
      cp -r --no-preserve=mode ${bse}/"Kuribo!" "$root/files/"
      cp --no-preserve=mode ${bse}/main.dol ${bse}/boot.bin "$root/sys/"
    '';
  };
in
base
// {
  isolate = true;
  inherit (disc) path;
  # Added to the platform's rather than replacing it: the base sets up Dolphin's
  # input configuration, which this game needs exactly as much as any other.
  preLaunch = base.preLaunch + disc.preLaunch;
}
