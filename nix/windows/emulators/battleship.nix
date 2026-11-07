# BattleShip's official Windows x64 release (the US build), unpacked: $out
# is the directory BattleShip.exe runs from, with torch.exe, config.yml and
# yamls/us/ for the first-run extraction it does itself, shipped as a
# bundle's emulator/. The same v1.6 as pkgs/battleship's AppImage.
#
# Portable (scripts/package-windows.ps1 leaves NON_PORTABLE off on
# purpose): saves, settings and BattleShip.o2r go to the working
# directory; torch.exe and config.yml are found beside the exe.
{
  lib,
  fetchurl,
  runCommand,
  unzip,
}:
let
  version = "1.6";
  zip = fetchurl {
    url = "https://github.com/JRickey/BattleShip/releases/download/v${version}/BattleShip-windows.zip";
    # sha256 a0c2ef997e7f561dc02da19b0e1134ecb9bfaac1d577d65ff04db3c7e27eadb4
    hash = "sha256-oMLvmX5/Vh3ALaGbDhE07Lm/qsHVd9Zf8E2zx+J+rbQ=";
  };
in
runCommand "battleship-windows-x64-${version}"
  {
    nativeBuildInputs = [ unzip ];
    passthru = {
      inherit version zip;
      exe = "BattleShip.exe";
    };
    meta = {
      description = "BattleShip ${version}, the official Windows x64 build";
      homepage = "https://github.com/JRickey/BattleShip";
      license = lib.licenses.unfree;
      sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
      platforms = lib.platforms.all;
    };
  }
  ''
    unzip -q ${zip} -d $out
    test -f $out/BattleShip.exe && test -f $out/torch.exe && test -f $out/config.yml
  ''
