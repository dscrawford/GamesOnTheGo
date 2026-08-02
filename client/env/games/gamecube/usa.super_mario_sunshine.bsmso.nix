# Super Mario Sunshine with Better Super Mario Sunshine Online —
# `gotg play usa.super_mario_sunshine bsmso`.
#
# BSMSO is BetterSunshineEngine plus two more Kuribo modules, so the disc side
# of it is the `bse` variant beside this file with more files copied in. Its own
# README describes the install this performs, and the BetterSunshineEngine.kxe
# it ships is byte-identical to the v4.0.0 release `helpers` already fetches.
#
# ONLINE PLAY DOES NOT WORK HERE, and that is the whole point of the mod, so it
# is worth being exact about why. The multiplayer is not in the disc modules:
# BSMSO.Launcher.exe drives it from outside, syncing player state by reading and
# writing the memory of the running Dolphin *process*. That launcher is a
# Windows PE32+ binary calling OpenProcess/ReadProcessMemory/WriteProcessMemory,
# and no Linux build is published. Wine cannot stand in, because those calls
# reach only processes inside the same Wine prefix — a native dolphin-emu is
# invisible to them. Running Windows Dolphin under Wine as well would be the way
# in, and it abandons every guarantee this flake exists to make.
#
# So what this variant gives is the disc: BSE, the Better Sunshine Moveset and
# the BSMSO module, with the mod's own title and option archives. The moveset is
# real and playable on your own. Hosting and joining are not available.
#
# NOT INSTALLED: the bundled CustomModels. The launcher syncs those into the
# game folder itself and the README does not say where they land, so guessing at
# a path would put files on the disc on the strength of an assumption. They are
# chosen from the launcher's dropdown in any case, which is the part that does
# not run. Same story for params.szs as in the `bse` variant.
{
  pkgs,
  base,
  helpers,
  gotgPkgs,
  ...
}:

let
  bse = helpers.betterSunshineEngine;

  release = pkgs.fetchzip {
    url = "https://gamebanana.com/dl/1771294"; # BSMSO v1.1, mods/697699
    hash = "sha256-xK3spKWQVdm+cx0OC+ZVpNzUfZ/1XLSeqMtiAhr1hhQ=";
    stripRoot = true;
    extension = "zip";
  };

  disc = helpers.kuriboSunshineDisc {
    inherit gotgPkgs;
    cache = "bsmso";
    # The install BSMSO's README_FIRST describes, minus the parts its launcher
    # does for itself. The engine goes on first because the modules load after
    # it and one of them names it a required parent.
    install = ''
      cp -r --no-preserve=mode ${bse}/"Kuribo!" "$root/files/"
      cp --no-preserve=mode ${bse}/main.dol ${bse}/boot.bin "$root/sys/"
      cp --no-preserve=mode ${release}/BetterSunshineMoveset.kxe ${release}/_BSMSO.kxe \
        "$root/files/Kuribo!/Mods/"
      # Overlays, not additions: both of these replace an archive the game
      # already ships in files/data/.
      cp -f --no-preserve=mode ${release}/assets/data/nintendo.szs ${release}/assets/data/option.szs \
        "$root/files/data/"
    '';
  };
in
base
// {
  isolate = true;
  inherit (disc) path;
  preLaunch = base.preLaunch + disc.preLaunch;
}
