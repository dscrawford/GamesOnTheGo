# The workspace as the builds see it: the manifests and the crates, never a
# checkout's target/ directory.
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
