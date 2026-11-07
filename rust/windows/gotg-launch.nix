# gotg.exe (rust/windows/crates/gotg-launch): called with pkgsCross.mingwW64 for the
# Windows bundle (flake package gotg-launch-windows), or natively for tests.
# mingwW64's gcc and rustc substitute from cache.nixos.org; nothing here
# builds a cross compiler.
{
  lib,
  stdenv,
  rustPlatform,
  windows ? null,
}:

rustPlatform.buildRustPackage {
  pname = "gotg-launch";
  version = "0.3.0";
  src = import ./source.nix { inherit lib; };
  cargoLock.lockFile = ./Cargo.lock;
  cargoBuildFlags = [
    "-p"
    "gotg-launch"
  ];
  cargoTestFlags = [
    "-p"
    "gotg-launch"
  ];
  # std on windows-gnu links winpthreads.
  buildInputs = lib.optional stdenv.hostPlatform.isWindows windows.pthreads;
  # A Windows binary's tests need Wine; checks.windowsLauncher runs it there.
  doCheck = stdenv.buildPlatform.canExecute stdenv.hostPlatform;

  meta = {
    description = "Play a GOTG game's Windows bundle: download, prepare, start";
    mainProgram = "gotg";
    platforms = lib.platforms.linux ++ lib.platforms.windows;
  };
}
