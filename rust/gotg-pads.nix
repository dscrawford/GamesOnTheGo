{
  lib,
  rustPlatform,
  pkg-config,
  sdl3,
}:

rustPlatform.buildRustPackage {
  pname = "gotg-pads";
  version = "0.3.0";
  src = import ./source.nix { inherit lib; };
  # The lock file is in the repo: nothing to keep in step by hand, and no
  # hash to go stale the next time a dependency is added.
  cargoLock.lockFile = ./Cargo.lock;
  cargoBuildFlags = [ "-p" "gotg-pads" ];
  cargoTestFlags = [ "-p" "gotg-pads" ];

  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ sdl3 ];

  meta = {
    description = "Report the controllers SDL can see, as JSON";
    mainProgram = "gotg-pads";
    platforms = lib.platforms.linux;
  };
}
