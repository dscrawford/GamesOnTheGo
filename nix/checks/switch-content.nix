# The Switch content reader, against containers built in the test: NCAs
# encrypted under keys the test makes up, so what is pinned is the layout and
# the crypto — header XTS, key-area ECB, section CTR — not any real dump.
{ pkgs }:
let
  python = pkgs.python3.withPackages (p: [
    p.cryptography
    p.pytest
  ]);
in
pkgs.runCommand "check-switch-content" { nativeBuildInputs = [ python ]; } ''
  export HOME=$TMPDIR
  cp ${../../src/client/env/switch/content.py} content.py
  cp ${../../tests/client/test_switch_content.py} test_switch_content.py
  python -m pytest -q -p no:cacheprovider test_switch_content.py
  touch $out
''
