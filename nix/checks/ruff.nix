# ruff over the Python that is ours.
{ pkgs }:
pkgs.runCommand "check-ruff"
  {
    nativeBuildInputs = [ pkgs.ruff ];
  }
  ''
    cd ${../../.}
    ruff check --no-cache src/gotg tests/conftest.py tests/service tests/indexer
    ruff format --no-cache --check src/gotg tests/conftest.py tests/service tests/indexer
    touch $out
  ''
