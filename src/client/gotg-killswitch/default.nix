{
  lib,
  stdenv,
  sdl3,
  libx11,
  pkg-config,
}:

stdenv.mkDerivation {
  pname = "gotg-killswitch";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ pkg-config ];
  # X11 for one property: under gamescope, "draw over the game" is
  # GAMESCOPE_EXTERNAL_OVERLAY rather than a window flag.
  buildInputs = [
    sdl3
    libx11
  ];
  strictDeps = true;

  buildPhase = ''
    runHook preBuild
    $CC -O2 -Wall -Wextra -o gotg-killswitch \
      main.c killswitch.c procstat.c overlay.c geometry.c \
      $(pkg-config --cflags --libs sdl3 x11) -lm
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
