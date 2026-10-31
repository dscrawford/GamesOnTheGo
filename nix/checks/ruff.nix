# ruff over the Python that is ours.
{ pkgs }:
let
  # Only what ruff reads, so a change elsewhere keeps this check's hash.
  src = pkgs.lib.fileset.toSource {
    root = ../../.;
    fileset = pkgs.lib.fileset.unions [
      ../../pyproject.toml
      ../../src/gotg
      ../../tests/conftest.py
      ../../tests/service
      ../../tests/indexer
    ];
  };
in
pkgs.runCommand "check-ruff"
  {
    nativeBuildInputs = [ pkgs.ruff ];
  }
  ''
    cd ${src}
    ruff check --no-cache src/gotg tests/conftest.py tests/service tests/indexer
    ruff format --no-cache --check src/gotg tests/conftest.py tests/service tests/indexer
    touch $out
  ''
