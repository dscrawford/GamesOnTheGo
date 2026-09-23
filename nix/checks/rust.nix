# The Rust workspace -- gotg-pads and gotg-killswitch -- tested, linted and
# formatted in one pass: `cargo test` for every crate, clippy with warnings
# as errors, and rustfmt's check. The packages also run their own crate's
# tests as they build; this is the gate that covers both and the style.
{ pkgs }:

pkgs.rustPlatform.buildRustPackage {
  pname = "check-rust";
  version = "0.3.0";
  src = import ../../rust/source.nix { inherit (pkgs) lib; };
  cargoLock.lockFile = ../../rust/Cargo.lock;
  GOTG_THEME = ../../config/theme.yaml;
  nativeBuildInputs = [
    pkgs.pkg-config
    pkgs.clippy
    pkgs.rustfmt
  ];
  buildInputs = [
    pkgs.sdl3
    pkgs.wayland
  ];
  buildPhase = ''
    runHook preBuild
    cargo fmt --all --check
    cargo clippy --offline --all-targets -- -D warnings
    cargo test --offline --workspace
    runHook postBuild
  '';
  # The default check and install phases both want binaries this does not make.
  doCheck = false;
  installPhase = "touch $out";
}
