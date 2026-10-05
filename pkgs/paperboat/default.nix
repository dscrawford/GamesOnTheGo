# PaperBoat — Harbour Masters' Paper Mario port.
#
# Built on the Paper Mario DX decompilation, rendering and input through
# libultraship, assets extracted from the player's ROM by the port itself.
# The same lineage as Ship of Harkinian and BattleShip, and packaged the way
# BattleShip is: upstream's Linux release is a zip holding an AppImage, and
# extracting that and patching it onto nixpkgs' libraries beats appimage-run
# -- no FUSE in the sandbox, and the result is an ordinary store path.
#
# The bundled libzip, spdlog and fmt are kept (exact sonames, majors nixpkgs
# does not carry, as with BattleShip); SDL2 is ours, so the port sees pads
# through the same SDL as every other game here.
#
# Where it looks for things, read from libultraship rather than guessed: it
# is a *portable* build, so its read-only files (paperboat.o2r, config.yml,
# assets/) are found beside /proc/self/exe, and everything it writes --
# pm64.o2r, saves/, paperboat.cfg.json, mods/ -- goes to $SHIP_HOME, or to
# the working directory when that is unset. makeWrapper execs the real
# binary, so /proc/self/exe is the store path below and the read-only half
# needs nothing from the caller.
#
# It ships no game assets: pm64.o2r is made on the player's machine from the
# player's cartridge dump. See src/client/env/games/n64/usa.paper_mario.paperboat.nix.
{
  lib,
  stdenv,
  fetchurl,
  appimageTools,
  autoPatchelfHook,
  makeWrapper,
  unzip,
  SDL2,
  libglvnd,
  libGL,
  zlib,
}:
let
  pname = "paperboat";
  version = "1.0.2";
  # Harbour Masters name releases, not versions; the asset carries the name.
  release = "Mulberry-Charlie";
  zip = fetchurl {
    url =
      "https://github.com/HarbourMasters/PaperBoat/releases/download/"
      + "${version}/Paperboat-${release}-Linux.zip";
    hash = "sha256-D5D2YuSRmHZ9SPe/OmhwMVYkte9/0faP3tqkbQfHSQg=";
  };
  unpacked = stdenv.mkDerivation {
    name = "${pname}-${version}-zip";
    src = zip;
    nativeBuildInputs = [ unzip ];
    sourceRoot = ".";
    installPhase = "mkdir -p $out && cp Paperboat.AppImage gamecontrollerdb.txt $out/";
  };
  extracted = appimageTools.extract {
    inherit pname version;
    src = "${unpacked}/Paperboat.AppImage";
  };
in
stdenv.mkDerivation {
  inherit pname version;
  src = extracted;

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];

  # The binary asks for little: SDL2 and libOpenGL by name. The X11, Wayland
  # and audio libraries are SDL's business and arrive with it. zlib is
  # libzip's.
  buildInputs = [
    SDL2
    libglvnd
    libGL
    zlib
  ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    share=$out/share/paperboat
    mkdir -p $share/lib
    # usr/bin/lib is libtinyxml2.a and cmake files -- build leftovers.
    cp -r usr/bin/Paperboat usr/bin/paperboat.o2r usr/bin/config.yml usr/bin/assets $share/
    # Ours replaces the bundled SDL2; the rest are kept, see the top.
    cp usr/lib/lib{zip,spdlog,fmt,bz2,zstd}* $share/lib/
    cp ${unpacked}/gamecontrollerdb.txt $share/
    chmod +x $share/Paperboat

    makeWrapper $share/Paperboat $out/bin/Paperboat \
      --prefix LD_LIBRARY_PATH : $share/lib \
      --suffix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libglvnd ]}
    runHook postInstall
  '';

  meta = {
    description = "PC port of Paper Mario on the Paper Mario DX decompilation and libultraship";
    homepage = "https://github.com/HarbourMasters/PaperBoat";
    license = lib.licenses.unfree; # decompiled game code; ROM supplied by the player
    platforms = [ "x86_64-linux" ];
    mainProgram = "Paperboat";
  };
}
