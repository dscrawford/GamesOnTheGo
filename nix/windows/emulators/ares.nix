# ares' own Windows x64 release, unpacked: $out is the directory ares.exe
# runs from (ares.exe, SDL3.dll, librashader.dll, Database/, Shaders/), which
# a bundle ships as emulator/. Not built here: the upstream zip, pinned.
#
# The same version as nixpkgs' pkgs.ares (what the Linux environments run),
# so a save state written on one is at least the same ares on the other.
# Bump the two together.
#
# Where it keeps its settings (desktop-ui/desktop-ui.cpp, locate()): a
# settings.bml beside ares.exe, else %LOCALAPPDATA%\ares\settings.bml, else
# %PROGRAMDATA%\ares\, else it creates one beside ares.exe. That is portable
# by default -- but one an installed ares already made in %LOCALAPPDATA%
# would win, and would be shared by every game, so the environments pass
# --settings-file {state}/settings.bml instead (env/emulators/ares.nix). Its
# window-geometry file (hiro/windows.bml) still goes beside the exe; nothing
# moves that, and it is harmless.
{
  lib,
  fetchurl,
  runCommand,
  unzip,
}:
let
  version = "148";
  # fetchurl rather than fetchzip: the hash is the release asset's own
  # sha256 as GitHub publishes it, checkable without unpacking anything.
  zip = fetchurl {
    name = "ares-windows-x64.zip";
    url = "https://github.com/ares-emulator/ares/releases/download/v${version}/ares-windows-x64.zip";
    # sha256 fa97958e1d359e3fe254353f87b6508f2762a959d7eb264c69965ad002037298
    hash = "sha256-+peVjh01nj/iVDU/h7ZQjydiqVnX6yZMaZZa0AIDcpg=";
  };
in
runCommand "ares-windows-x64-${version}"
  {
    nativeBuildInputs = [ unzip ];
    passthru = {
      inherit version zip;
      exe = "ares.exe";
    };
    meta = {
      description = "ares, the official Windows x64 build, for GOTG's Windows bundles";
      homepage = "https://ares-emu.net";
      license = lib.licenses.isc;
      sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
      platforms = lib.platforms.all;
    };
  }
  ''
    unzip -q ${zip} -d unpacked
    # One directory in the zip, ares-v${version}/; its contents are $out.
    mv unpacked/ares-v${version} $out
    test -f $out/ares.exe
  ''
