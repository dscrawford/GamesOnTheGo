# BattleShip — the Super Smash Bros. (N64) PC port.
#
# Built on the ssb-decomp-re decompilation, rendering and input through
# libultraship, assets extracted from the player's ROM by Torch. The same
# lineage as the Ship of Harkinian ports gotg already runs for Ocarina of
# Time and Majora's Mask, which is why the game env can lean on the shared
# helper rather than inventing a first-run dance.
#
# Why it matters here: four pads, plug and play, plus a two-player co-op of
# the originally single-player Classic mode. That is the whole point of this
# box, and it is a mode the N64 cartridge does not have.
#
# Upstream ships an AppImage. Extracting it and patching the contents onto
# nixpkgs' libraries beats running it through appimage-run: no FUSE inside
# the sandbox, and the result is an ordinary store path.
#
# Four of its bundled libraries are kept rather than replaced — libzip,
# tinyxml2, spdlog and fmt are all linked by exact soname, and nixpkgs
# carries different majors of each. They live in the output, so
# autoPatchelfHook patches them onto our glibc along with everything else.
#
# It ships no game assets: BattleShip.o2r is made on the player's machine
# from the player's cartridge dump. See
# src/client/env/games/n64/usa.super_smash_bros.nix.
{
  lib,
  stdenv,
  fetchurl,
  appimageTools,
  autoPatchelfHook,
  makeWrapper,
  SDL2,
  libglvnd,
  udev,
  libpulseaudio,
  alsa-lib,
  wayland,
  libxkbcommon,
  libx11,
  libxext,
  libxrandr,
  libxi,
  libxcursor,
  libxfixes,
  libxinerama,
  libxrender,
  libGL,
  vulkan-loader,
  zlib,
  libgpg-error,
}:
let
  pname = "battleship";
  version = "1.6";
  src = fetchurl {
    url =
      "https://github.com/JRickey/BattleShip/releases/download/"
      + "v${version}/BattleShip-x86_64.AppImage";
    hash = "sha256-x7kJ6CyZHMiUeszg87RwKVm0zb2XgfwZmw3PgBW4fzU=";
  };
  extracted = appimageTools.extract { inherit pname version src; };
in
stdenv.mkDerivation {
  inherit pname version;
  src = extracted;

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];

  buildInputs = [
    SDL2
    libglvnd
    libGL
    vulkan-loader
    udev
    # Wanted by two of the bundled libraries rather than by the binary:
    # zlib by libzip, libgpg-error by libgcrypt.
    zlib
    libgpg-error
    libpulseaudio
    alsa-lib
    wayland
    libxkbcommon
    libx11
    libxext
    libxrandr
    libxi
    libxcursor
    libxfixes
    libxinerama
    libxrender
  ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/battleship
    cp -r usr/bin usr/lib usr/share/BattleShip/. $out/share/battleship/
    chmod +x $out/share/battleship/bin/BattleShip $out/share/battleship/bin/torch

    # No --chdir here, unlike the recomp ports beside it. This one reads
    # BattleShip.o2r out of its working directory, and that archive is made
    # on the player's machine — so the caller has to be able to put it
    # somewhere writable and run from there. The game env does exactly that.
    for exe in BattleShip torch; do
      makeWrapper $out/share/battleship/bin/$exe $out/bin/$exe \
        --prefix LD_LIBRARY_PATH : $out/share/battleship/lib \
        --suffix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ vulkan-loader libglvnd ]}
    done

    # What the port needs beside it at runtime, named once so the env can
    # link the set without knowing what is in it.
    ln -s $out/share/battleship $out/share/battleship/runtime
    runHook postInstall
  '';

  meta = {
    description = "PC port of Super Smash Bros. (N64) on ssb-decomp-re and libultraship";
    homepage = "https://github.com/JRickey/BattleShip";
    license = lib.licenses.unfree; # decompiled game code; ROM supplied by the player
    platforms = [ "x86_64-linux" ];
    mainProgram = "BattleShip";
  };
}
