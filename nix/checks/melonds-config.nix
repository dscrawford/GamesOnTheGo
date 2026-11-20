# The melonDS.toml setter the DS environment's preLaunch runs.
{ pkgs }:
let
  python = pkgs.python3.withPackages (p: [
    p.tomlkit
    p.pytest
  ]);
in
pkgs.runCommand "check-melonds-config" { nativeBuildInputs = [ python ]; } ''
  export HOME=$TMPDIR
  cp ${../../src/client/env/emulators/melonds-config.py} melonds_config.py
  cp ${../../tests/client/test_melonds_config.py} test_melonds_config.py
  python -m pytest -q -p no:cacheprovider test_melonds_config.py
  touch $out
''
