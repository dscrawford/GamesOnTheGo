# 2 Ship 2 Harkinian's official Windows x64 release, unpacked: $out is the
# directory 2ship.exe runs from (2ship.exe, 2ship.o2r, assets/ for its
# built-in extractor), shipped as a bundle's emulator/. The same 5.0.1 as
# nixpkgs' _2ship2harkinian, which the Linux environment runs. Note the
# exe's name: 2ship.exe here, 2s2h on Linux.
#
# Portable like Ship of Harkinian (no NON_PORTABLE in its release
# workflow): its data directory is the working directory.
{
  lib,
  fetchurl,
  runCommand,
  unzip,
}:
let
  version = "5.0.1";
  zip = fetchurl {
    url = "https://github.com/HarbourMasters/2ship2harkinian/releases/download/${version}/2Ship-Battler-Bravo-Win64.zip";
    # sha256 a46664a01623ad5d7cc82d29a9889699f6a98c9501c7a959a089f34380c1f078
    hash = "sha256-pGZkoBYjrV18yC0pqYiWmfapjJUBx6lZoInzQ4DB8Hg=";
  };
in
runCommand "2ship2harkinian-windows-x64-${version}"
  {
    nativeBuildInputs = [ unzip ];
    passthru = {
      inherit version zip;
      exe = "2ship.exe";
    };
    meta = {
      description = "2 Ship 2 Harkinian ${version}, the official Windows x64 build";
      homepage = "https://github.com/HarbourMasters/2ship2harkinian";
      license = lib.licenses.unfree;
      sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
      platforms = lib.platforms.all;
    };
  }
  ''
    unzip -q ${zip} -d $out
    rm -r $out/debug
    test -f $out/2ship.exe && test -f $out/2ship.o2r && test -d $out/assets
  ''
