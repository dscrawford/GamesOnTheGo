# Snowboard Kids 2: Recompiled — the N64Recomp/RT64 static recompilation.
#
# Same shape as pkgs/dk64recomp beside it, and for the same reason: upstream
# ships a prebuilt x86-64 Linux binary, so this patches it onto nixpkgs'
# libraries rather than rebuilding the recompiled sources, which would buy
# nothing a QA run could see.
#
# What the port is for, in one line: RT64 draws the game at the display's
# refresh rate instead of the N64's 30fps cap, and "changing framerate has no
# effect on gameplay" — the runtime decouples the two, so this is not the
# speed-up that fast-forwarding an emulator gives.
#
# It ships no game assets. The ROM is the player's, and gotg stages it — see
# src/client/env/games/n64/usa.snowboard_kids_2.nix.
#
# Note the sequel, not the original: Snowboard Kids 1 is decompiled
# (tenry92/sbk-decomp) but has no port, so it still runs under ares.
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  SDL2,
  vulkan-loader,
  libGL,
  libx11,
  libxext,
  libsm,
  libice,
  libxrandr,
  libxi,
  libxcursor,
  libxfixes,
  libxkbcommon,
  wayland,
  alsa-lib,
  pipewire,
  libpulseaudio,
  udev,
  # gtk3 is the launcher's file picker, curl fetches the mod registry, and
  # freetype draws the RmlUi menus. The rest of the GTK stack — pango, cairo,
  # atk, gdk-pixbuf, harfbuzz — arrives in its closure; naming it is enough.
  gtk3,
  curl,
  freetype,
}:
stdenv.mkDerivation (finalAttrs: {
  pname = "snowboardkids2recomp";
  version = "1.0.5";

  # A plain tarball, unlike DK64's zip-around-a-tarball, so the default
  # unpackPhase handles it.
  src = fetchurl {
    url =
      "https://github.com/cdlewis/snowboardkids2-recomp/releases/download/"
      + "v${finalAttrs.version}/SnowboardKids2Recompiled-Linux-X64-Release.tar.gz";
    hash = "sha256-kKl9Tu2Dv8OpYiEsCeTUlDwTEgDNEAkLjnpdgL1OdzI=";
  };

  # The tarball has no top-level directory — it unpacks the binary, assets/
  # and the controller database straight into the current directory.
  sourceRoot = ".";

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
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
    libx11
    libxext
    libsm
    libice
    libxrandr
    libxi
    libxcursor
    libxfixes
  ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/snowboardkids2recomp
    cp -r SnowboardKids2Recompiled assets recompcontrollerdb.txt \
      $out/share/snowboardkids2recomp/
    chmod +x $out/share/snowboardkids2recomp/SnowboardKids2Recompiled

    # It loads assets/ relative to the working directory, so the wrapper is
    # what makes it runnable from anywhere — and RT64 needs the Vulkan loader
    # to find a driver, which outside a NixOS session means being told.
    makeWrapper $out/share/snowboardkids2recomp/SnowboardKids2Recompiled \
      $out/bin/SnowboardKids2Recompiled \
      --chdir $out/share/snowboardkids2recomp \
      --suffix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ vulkan-loader ]}
    runHook postInstall
  '';

  meta = {
    description = "Static recompilation of Snowboard Kids 2 on the N64Recomp/RT64 stack";
    homepage = "https://github.com/cdlewis/snowboardkids2-recomp";
    license = lib.licenses.unfree; # recompiled game code; ROM supplied by the player
    platforms = [ "x86_64-linux" ];
    mainProgram = "SnowboardKids2Recompiled";
  };
})
