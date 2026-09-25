{
  lib,
  rustPlatform,
  pkg-config,
  sdl3,
  wayland,
  mesa,
  runtimeShell,
  # What the bar shares with the picker, read at build time (the crate's
  # build.rs): its palette, which drawing stands for which controller, and
  # the drawings.
  theme,
  iconRules,
  iconArt,
  controllerArt,
  # config/controllers: each console's layout, drawing and anchors, for the
  # in-game rebind.
  controllerConfig,
}:

let
  # The nixGL exports every game environment has: on SteamOS a nix-built
  # program finds no GPU userspace, and the painter came up with "Could not
  # get EGL display" from every renderer and drew nothing over the game.
  # Only where the host has no /run/opengl-driver; see foreign-gl.nix.
  foreignGl = (import ../src/client/env/foreign-gl.nix { inherit mesa; }).guarded;
in
rustPlatform.buildRustPackage {
  pname = "gotg-killswitch";
  version = "0.3.0";
  src = import ./source.nix { inherit lib; };
  cargoLock.lockFile = ./Cargo.lock;
  cargoBuildFlags = [ "-p" "gotg-killswitch" ];
  cargoTestFlags = [ "-p" "gotg-killswitch" ];
  GOTG_THEME = theme;
  GOTG_ICON_RULES = iconRules;
  GOTG_ICON_ART = iconArt;
  GOTG_CONTROLLER_ART = controllerArt;
  GOTG_CONTROLLER_CONFIG = controllerConfig;

  nativeBuildInputs = [ pkg-config ];
  # SDL for the pads and the window; libwayland for layer-shell on SDL's own
  # surface. X11 is spoken by x11rb, in Rust, over its own connection.
  buildInputs = [
    sdl3
    wayland
  ];

  # A script in front, not makeWrapper: the exports are decided at run time.
  # The painter is /proc/self/exe -- the binary, not this script -- and
  # inherits what the script exported.
  postInstall = ''
    mkdir -p $out/libexec
    mv $out/bin/gotg-killswitch $out/libexec/gotg-killswitch
    cat >$out/bin/gotg-killswitch <<'EOF'
    #!${runtimeShell}
    ${foreignGl}
    exec @out@/libexec/gotg-killswitch "$@"
    EOF
    substituteInPlace $out/bin/gotg-killswitch --replace-fail @out@ $out
    chmod 755 $out/bin/gotg-killswitch
  '';

  meta = {
    description = "Stops a running game when both shoulders and Start are held, and draws the overlay bar over it";
    mainProgram = "gotg-killswitch";
    platforms = lib.platforms.linux;
  };
}
