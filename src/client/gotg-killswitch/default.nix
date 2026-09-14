{
  lib,
  stdenv,
  sdl3,
  pkg-config,
}:

stdenv.mkDerivation {
  pname = "gotg-killswitch";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ sdl3 ];
  strictDeps = true;

  buildPhase = ''
    runHook preBuild
    $CC -O2 -Wall -Wextra -o gotg-killswitch main.c killswitch.c procstat.c $(pkg-config --cflags --libs sdl3)
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 gotg-killswitch $out/bin/gotg-killswitch
    runHook postInstall
  '';

  meta = {
    description = "Stops a running game when both shoulders and Start are held";
    mainProgram = "gotg-killswitch";
    platforms = lib.platforms.linux;
  };
}
