# Donkey Kong 64: Recompiled — the N64Recomp/RT64 static recompilation.
#
# The upstream release is a prebuilt x86-64 binary, so this patches it onto
# nixpkgs' libraries rather than building it: the project's own CI produces
# the recompiled sources and links them, and reproducing that here would buy
# nothing a QA-visible difference could be attributed to.
#
# It ships no game assets — the ROM is the player's, and gotg stages it (see
# src/client/env/games/n64/usa.donkey_kong_64.nix), so the launcher's ROM
# picker never has anything to ask for.
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  unzip,
  SDL2,
  vulkan-loader,
  libGL,
  xorg,
  libxkbcommon,
  wayland,
  alsa-lib,
  pipewire,
  libpulseaudio,
  udev,
  # gtk3 is the launcher's file picker, curl fetches the mod registry
  # (dk64recomp.com/mods.json), and freetype draws the menus. The rest of the
  # GTK stack — pango, cairo, atk, gdk-pixbuf, harfbuzz — arrives in its
  # closure; naming it is enough.
  gtk3,
  curl,
  freetype,
}:
stdenv.mkDerivation (finalAttrs: {
  pname = "dk64recomp";
  version = "1.0.2";

  # The release zip holds a tarball; unpackPhase takes the zip apart and the
  # tarball inside it, in that order.
  src = fetchurl {
    url =
      "https://github.com/Rainchus/Donkey-Kong-64-Recompiled/releases/download/"
      + "${finalAttrs.version}/DK64Recompiled-Linux-X64-Release-"
      + "${lib.replaceStrings [ "." ] [ "-" ] finalAttrs.version}.zip";
    hash = "sha256-enIygNknaLvrHDhPX76fJ80I/a2LkN+FqUAqFR0x5N8=";
  };

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
    unzip
  ];

  buildInputs = [
    SDL2
    vulkan-loader
    libGL
    gtk3
    curl
    freetype
    libxkbcommon
    wayland
    alsa-lib
    pipewire
    libpulseaudio
    udev
    xorg.libX11
    xorg.libXext
    xorg.libSM
    xorg.libICE
    xorg.libXrandr
    xorg.libXi
    xorg.libXcursor
    xorg.libXfixes
  ];

  unpackPhase = ''
    runHook preUnpack
    unzip -q $src
    tar xzf DK64Recompiled.tar.gz
    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/dk64recomp
    cp -r DK64Recompiled assets recompcontrollerdb.txt $out/share/dk64recomp/
    chmod +x $out/share/dk64recomp/DK64Recompiled

    # It loads assets/ relative to the working directory, so the wrapper is
    # what makes it runnable from anywhere — and RT64 needs the Vulkan loader
    # to find a driver, which outside a NixOS session means being told.
    makeWrapper $out/share/dk64recomp/DK64Recompiled $out/bin/DK64Recompiled \
      --chdir $out/share/dk64recomp \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ vulkan-loader ]}
    runHook postInstall
  '';

  meta = {
    description = "Static recompilation of Donkey Kong 64 on the N64Recomp/RT64 stack";
    homepage = "https://dk64recomp.com/";
    license = lib.licenses.unfree; # recompiled game code; ROM supplied by the player
    platforms = [ "x86_64-linux" ];
    mainProgram = "DK64Recompiled";
  };
})
