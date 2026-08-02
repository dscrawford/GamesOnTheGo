# Super Mario Sunshine with BetterSunshineEngine — `gotg play usa.super_mario_sunshine bse`.
#
# BetterSunshineEngine is not a launcher setting; it changes the game's own
# files. Installing it means opening the disc image, adding the Kuribo! code
# loader and its module, replacing main.dol and boot.bin, and building the image
# back up again. So this patches once on the first launch and keeps the result
# beside the game's saves — the downloaded image in ~/Games is never touched,
# and removing the mod is deleting one directory.
#
# NOT INSTALLED: the six surfing parameters. The mod's README asks for them to
# be written *inside* the game's params.szs, which is a Yaz0 RARC archive.
# wszst can read that container but not write one, and nothing packaged can — so
# the engine and its module install in full while the surfing mechanic keeps the
# game's stock values. When a RARC writer exists, that step goes here.
{ pkgs, base, gotgPkgs, ... }:

let
  release = pkgs.fetchzip {
    url = "https://github.com/DotKuribo/BetterSunshineEngine/releases/download/v4.0.0/BetterSunshineEngine_RELEASE.zip";
    hash = "sha256-haAhVj5sg/xpLXhWDphBFAgeJp89bhxR4N1KR4nFedw=";
    stripRoot = true;
  };
in
base
// {
  isolate = true;

  path = [
    pkgs.dolphin-emu # dolphin-tool: converts between disc formats
    gotgPkgs.pyisotools # extracts and rebuilds the disc itself
  ];

  # Added to the platform's rather than replacing it: the base sets up Dolphin's
  # input configuration, which this game needs exactly as much as any other.
  preLaunch = base.preLaunch + ''
    bse="$state/bse"
    patched="$bse/super_mario_sunshine.rvz"

    if [ ! -f "$patched" ]; then
      echo "first run: installing BetterSunshineEngine into a copy of the game" >&2
      rm -rf "$bse/build" "$bse/game.iso" "$bse/patched.iso"
      mkdir -p "$bse"

      # pyisotools reads a plain ISO, not the RVZ the library stores.
      dolphin-tool convert -f iso -i "$target" -o "$bse/game.iso"
      pyisotools "$bse/game.iso" E --dest "$bse/build"

      # It unpacks into a "root" directory beneath the destination.
      root="$bse/build/root"
      cp -r ${release}/"Kuribo!" "$root/files/"
      cp ${release}/main.dol ${release}/boot.bin "$root/sys/"
      # Everything above came out of the store read-only.
      chmod -R u+w "$root"

      # pyisotools rather than wiimms-iso-tools: the latter rebuilt this
      # GameCube game as a *Wii* disc, partition wrapper and all, which Dolphin
      # loaded happily and the console then failed to boot. The mod's README
      # names pyisotools as a supported rebuilder, and it is the one that works.
      pyisotools "$root" B --dest "$bse/patched.iso"
      dolphin-tool convert -f rvz -b 131072 -c zstd -l 5 \
        -i "$bse/patched.iso" -o "$patched"
      rm -rf "$bse/build" "$bse/game.iso" "$bse/patched.iso"
    fi

    # Launch the patched copy instead of the download. The wrapper's arguments
    # are written in terms of $target, so redirecting it here is all it takes.
    target="$patched"
  '';
}
