# Ship of Harkinian's official Windows x64 release, unpacked: $out is the
# directory soh.exe runs from (soh.exe, soh.o2r, assets/ for its built-in
# extractor, gamecontrollerdb.txt), shipped as a bundle's emulator/. The
# same 9.3.0 as pkgs/shipwright, so an archive or a save written by one is
# the other's. Bump the two together.
#
# A portable build (no NON_PORTABLE in the release workflow), so
# libultraship's app directory is "." -- the working directory -- and
# SHIP_HOME is only read on Linux and macOS. The facet starts it in
# {state}/data/soh (env/emulators/harkinian.nix; docs/windows.md).
{
  lib,
  fetchurl,
  runCommand,
  unzip,
}:
let
  version = "9.3.0";
  zip = fetchurl {
    url = "https://github.com/HarbourMasters/Shipwright/releases/download/${version}/SoH-Dewey-Alpha-Win64.zip";
    # sha256 bf46cfc41be17f3b63901db46dac46ac2a63ed3851aa13e3ba6c9ec5d0ac2632
    hash = "sha256-v0bPxBvhfztjkB20baxGrCpj7ThRqhPjumyexdCsJjI=";
  };
in
runCommand "shipwright-windows-x64-${version}"
  {
    nativeBuildInputs = [ unzip ];
    passthru = {
      inherit version zip;
      exe = "soh.exe";
    };
    meta = {
      description = "Ship of Harkinian ${version}, the official Windows x64 build";
      homepage = "https://www.shipofharkinian.com";
      license = lib.licenses.unfree;
      sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
      platforms = lib.platforms.all;
    };
  }
  ''
    unzip -q ${zip} -d $out
    # Debug symbols, for a crash dump nobody here reads.
    rm -r $out/debug
    test -f $out/soh.exe && test -f $out/soh.o2r && test -d $out/assets
  ''
