# Super Mario Sunshine's Kuribo mods. A mod is not an emulator shape — it
# changes a single game's own files — so it lives beside the other mods rather
# than with the launchers.
{ pkgs }:
{
  # The official BetterSunshineEngine release. Both Sunshine variants install it:
  # BSMSO ships its own copy of the module, byte-identical to this one, and names
  # BSE its "required parent".
  betterSunshineEngine = pkgs.fetchzip {
    url = "https://github.com/DotKuribo/BetterSunshineEngine/releases/download/v4.0.0/BetterSunshineEngine_RELEASE.zip";
    hash = "sha256-haAhVj5sg/xpLXhWDphBFAgeJp89bhxR4N1KR4nFedw=";
    stripRoot = true;
  };

  # Super Mario Sunshine carrying Kuribo modules — shared by the `bse` and
  # `bsmso` variants, which differ only in which files go onto the disc.
  #
  # A Kuribo mod is not a launcher setting: it changes the game's own files. So
  # this opens the disc image, writes the mod in and builds the image back up,
  # keeping the result beside the game's saves. The download in ~/Games is never
  # touched, and removing a mod is deleting one directory.
  #
  # `install` is a shell fragment run with $root at the extracted disc, which is
  # the sys/ and files/ pair both mod READMEs are written in terms of. It must
  # copy with --no-preserve=mode: everything it draws on comes out of the store
  # read-only, and a mod that writes *into* a directory an earlier line copied
  # from there — a module joining Kuribo!/Mods, say — fails on permissions
  # otherwise.
  kuriboSunshineDisc =
    {
      gotgPkgs,
      cache,
      install,
    }:
    {
      path = [
        pkgs.dolphin-emu # dolphin-tool: converts between disc formats
        gotgPkgs.pyisotools # extracts and rebuilds the disc itself
      ];
      preLaunch = ''
        modded="$state/${cache}"
        patched="$modded/super_mario_sunshine.rvz"

        if [ ! -f "$patched" ]; then
          echo "first run: installing ${cache} into a copy of the game" >&2
          # An interrupted run can leave a tree that cannot be deleted: entries
          # copied from the store are read-only, and unlinking one needs write
          # permission on the directory holding it. Without this a single failed
          # first run wedges the environment for good, since every later launch
          # fails on the same rm.
          chmod -R u+w "$modded/build" 2>/dev/null || true
          rm -rf "$modded/build" "$modded/game.iso" "$modded/patched.iso"
          mkdir -p "$modded"

          # pyisotools reads a plain ISO, not the RVZ the library stores.
          dolphin-tool convert -f iso -i "$target" -o "$modded/game.iso"
          pyisotools "$modded/game.iso" E --dest "$modded/build"

          # It unpacks into a "root" directory beneath the destination.
          root="$modded/build/root"
          ${install}
          # Everything copied in came out of the store read-only.
          chmod -R u+w "$root"

          # pyisotools rather than wiimms-iso-tools: the latter rebuilt this
          # GameCube game as a *Wii* disc, partition wrapper and all, which
          # Dolphin loaded happily and the console then failed to boot. Both mod
          # READMEs name pyisotools as a supported rebuilder, and a rebuilt image
          # has been booted headless with OSREPORT logging on to watch Kuribo
          # name each module as it loads it — which is the only check that
          # actually distinguishes a working install from a plausible one.
          pyisotools "$root" B --dest "$modded/patched.iso"
          dolphin-tool convert -f rvz -b 131072 -c zstd -l 5 \
            -i "$modded/patched.iso" -o "$patched"
          rm -rf "$modded/build" "$modded/game.iso" "$modded/patched.iso"
        fi

        # Launch the patched copy instead of the download. The wrapper's
        # arguments are written in terms of $target, so redirecting it here is
        # all it takes.
        target="$patched"
      '';
    };

}
