# Open Nectar — the native Pikmin port.
#
# Built on the projectPiki decompilation, which reached 100% in July 2026.
# Not emulation and not Dolphin: the game's own code compiled for x86-64,
# with GX translated to OpenGL. 30, 60 or 120 fps, widescreen that lays the
# HUD out for 16:9 rather than stretching it, and the original JAudio engine
# for sound.
#
# Up to 0.6 upstream shipped a self-contained tarball -- its own glibc, its
# own loader, its own SDL2 -- built against a glibc newer than nixpkgs' and so
# impossible to patch onto it: this package ran the bundled loader, with a
# script choosing the host's GL or ours. 0.9 dropped the bundle. The binaries
# now want glibc 2.29 and link SDL2 in, asking the system only for libGL and
# libX11, and dlopening the rest (Wayland, the X extensions, the sound
# servers, udev) the way SDL does. So it is patched like every other
# prebuilt port here, and GL is nothing special any more: the host's when
# there is a /run/opengl-driver, nixpkgs' mesa when there is not -- which
# env/foreign-gl.nix arranges for every environment alike.
#
# Two programs matter. `nectar-launcher` verifies a disc image and extracts
# its assets, and takes --rom/--install-dir/--extract-only so that it can do
# that with no desktop session at all. `nectar` is the game, and it reads its
# assets relative to the working directory. The game env drives both; see
# src/client/env/games/gamecube/usa.pikmin_rev1.nix.
#
# nectar-pal is the European build. gotg's entry is the USA disc, so it is
# installed for completeness rather than for use.
#
# It ships no game assets: they come out of the player's own disc image.
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  libGL,
  libx11,
  libxext,
  libxcursor,
  libxfixes,
  libxi,
  libxrandr,
  libxscrnsaver,
  libxkbcommon,
  wayland,
  alsa-lib,
  libpulseaudio,
  udev,
  dbus,
}:
stdenv.mkDerivation (finalAttrs: {
  pname = "open-nectar";
  version = "0.9.1";

  src = fetchurl {
    url =
      "https://github.com/SSunnKing/Open-Nectar---Pikmin-Native-PC-Mobile-Port/"
      + "releases/download/${finalAttrs.version}/nectar-linux.tar.gz";
    hash = "sha256-vdjyv+2HxdjGYPd54YavIMVTBrKlcUMIY+RuxERV/Y8=";
  };

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];

  buildInputs = [
    libGL
    libx11
  ];

  # What the built-in SDL2 dlopens rather than links: without these on the
  # RUNPATH it finds no video or audio driver at all.
  runtimeDependencies = [
    libxext
    libxcursor
    libxfixes
    libxi
    libxrandr
    libxscrnsaver
    libxkbcommon
    wayland
    alsa-lib
    libpulseaudio
    udev
    dbus
  ];

  dontConfigure = true;
  dontBuild = true;

  # The game finds its assets relative to the working directory, which the
  # environment sets, so the wrappers only exist to keep the binaries in
  # share/ beside the launcher that looks for them there.
  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/open-nectar $out/bin
    install -m755 nectar nectar-pal nectar-launcher $out/share/open-nectar/
    install -m644 README.txt open_nectar.png $out/share/open-nectar/
    for exe in nectar nectar-pal nectar-launcher; do
      makeWrapper $out/share/open-nectar/$exe $out/bin/$exe
    done
    runHook postInstall
  '';

  meta = {
    description = "Native PC port of Pikmin, built on the projectPiki decompilation";
    homepage = "https://github.com/SSunnKing/Open-Nectar---Pikmin-Native-PC-Mobile-Port";
    license = lib.licenses.unfree; # decompiled game code; disc image supplied by the player
    platforms = [ "x86_64-linux" ];
    mainProgram = "nectar";
  };
})
