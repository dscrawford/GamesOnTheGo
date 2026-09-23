{
  lib,
  stdenv,
  sdl3,
  libx11,
  libxext,
  wayland,
  wayland-scanner,
  wayland-protocols,
  wlr-protocols,
  cjson,
  pkg-config,
  yq-go,
  mesa,
  runtimeShell,
  # The picker's palette, which the bar is drawn in: see gen-theme.sh.
  theme,
}:

let
  # The nixGL exports every game environment has: on SteamOS a nix-built
  # program finds no GPU userspace, and the painter came up with "Could not
  # get EGL display" from every renderer and drew nothing over the game.
  # Only where the host has no /run/opengl-driver; see foreign-gl.nix.
  foreignGl = (import ../env/foreign-gl.nix { inherit mesa; }).guarded;
in
stdenv.mkDerivation {
  pname = "gotg-killswitch";
  version = "0.2.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [
    pkg-config
    wayland-scanner
    yq-go
  ];
  # X11 for gamescope's overlay property and for an input shape nothing can
  # click; Wayland for layer-shell, the way over a fullscreen game on sway;
  # cJSON for padmap's events, which are JSON from another program.
  buildInputs = [
    sdl3
    libx11
    libxext
    wayland
    cjson
  ];
  strictDeps = true;

  buildPhase = ''
    runHook preBuild
    # layer-shell's popups are xdg-shell's, so its generated code refers to
    # xdg_popup_interface: both are generated, and both linked.
    layer=${wlr-protocols}/share/wlr-protocols/unstable/wlr-layer-shell-unstable-v1.xml
    xdg=${wayland-protocols}/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml
    wayland-scanner client-header "$layer" wlr-layer-shell-unstable-v1-client-protocol.h
    wayland-scanner private-code "$layer" wlr-layer-shell-unstable-v1-protocol.c
    wayland-scanner client-header "$xdg" xdg-shell-client-protocol.h
    wayland-scanner private-code "$xdg" xdg-shell-protocol.c
    sh gen-theme.sh ${theme} > theme.h

    $CC -O2 -std=c17 -Wall -Wextra -I. -o gotg-killswitch \
      main.c killswitch.c procstat.c overlay.c pairing.c bar.c shapes.c scene.c events.c padlink.c \
      frame.c painter.c \
      wlr-layer-shell-unstable-v1-protocol.c xdg-shell-protocol.c \
      $(pkg-config --cflags --libs sdl3 x11 xext wayland-client libcjson) -lm
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 gotg-killswitch $out/libexec/gotg-killswitch
    # A script in front, not makeWrapper: the exports are decided at run
    # time. The painter is /proc/self/exe -- the binary, not this script --
    # and inherits what the script exported.
    mkdir -p $out/bin
    cat >$out/bin/gotg-killswitch <<'EOF'
    #!${runtimeShell}
    ${foreignGl}
    exec @out@/libexec/gotg-killswitch "$@"
    EOF
    substituteInPlace $out/bin/gotg-killswitch --replace-fail @out@ $out
    chmod 755 $out/bin/gotg-killswitch
    runHook postInstall
  '';

  meta = {
    description = "Stops a running game when both shoulders and Start are held, and draws the overlay bar over it";
    mainProgram = "gotg-killswitch";
    platforms = lib.platforms.linux;
  };
}
