# Dolphin's official Windows x64 release, unpacked: $out is the directory
# with Dolphin.exe and its DLLs, shipped as a bundle's emulator/. 2609 is the
# tag nixpkgs' dolphin-emu builds for Linux here, so a memory card or state
# moves between the two with one emulator version on both sides.
{
  lib,
  fetchurl,
  runCommand,
  _7zz,
}:
let
  version = "2609";
  archive = fetchurl {
    url = "https://dl.dolphin-emu.org/releases/${version}/dolphin-${version}-x64.7z";
    hash = "sha256-GqJXhbfUy3Yll1TF+83YhWhXuOe7TlI8V6isVmZfunU=";
  };
in
runCommand "dolphin-windows-${version}"
  {
    nativeBuildInputs = [ _7zz ];
    passthru = { inherit version archive; };
    meta = {
      description = "Dolphin ${version}, the official Windows x64 build";
      homepage = "https://dolphin-emu.org";
      license = lib.licenses.gpl2Plus;
      platforms = lib.platforms.all;
    };
  }
  ''
    7zz x -bd -y -o"$TMPDIR/x" ${archive} >/dev/null
    # Updater.exe stays; the facet's empty AutoUpdate track keeps the pin.
    mv "$TMPDIR/x/Dolphin-x64" "$out"
    test -f "$out/Dolphin.exe"
  ''
