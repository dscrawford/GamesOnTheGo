# The 3DS CIA reader, against CIAs laid out in the test.
{ pkgs }:
let
  python = pkgs.python3.withPackages (p: [ p.pytest ]);
in
pkgs.runCommand "check-3ds-cia" { nativeBuildInputs = [ python ]; } ''
  export HOME=$TMPDIR
  cp ${../../src/client/env/3ds/cia.py} cia.py
  cp ${../../tests/client/test_3ds_cia.py} test_3ds_cia.py
  python -m pytest -q -p no:cacheprovider test_3ds_cia.py
  touch $out
''
