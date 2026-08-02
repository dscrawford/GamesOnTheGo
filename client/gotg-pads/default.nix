{
  lib,
  stdenv,
  sdl3,
  pkg-config,
}:

stdenv.mkDerivation {
  pname = "gotg-pads";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ sdl3 ];
  strictDeps = true;

  buildPhase = ''
    runHook preBuild
    $CC -O2 -Wall -Wextra -o gotg-pads gotg-pads.c $(pkg-config --cflags --libs sdl3)
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 gotg-pads $out/bin/gotg-pads
    runHook postInstall
  '';

  meta = {
    description = "Report the controllers SDL can see, as JSON";
    mainProgram = "gotg-pads";
    platforms = lib.platforms.linux;
  };
}
