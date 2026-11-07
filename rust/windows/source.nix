# The Windows workspace as the builds see it: manifests and crates only.
{ lib }:
lib.fileset.toSource {
  root = ./.;
  fileset = lib.fileset.unions [
    ./Cargo.toml
    ./Cargo.lock
    ./rustfmt.toml
    ./crates
  ];
}
