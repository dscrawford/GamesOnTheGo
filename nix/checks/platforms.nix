# A DAT directory mapped to a platform with no environment imports games
# nobody can launch. plan.py is pure stdlib, so this reads the real map rather
# than a copy of it.
#
# The indexer venv rather than a bare python: this reads rules.yaml as well as
# plan.py now, and rules.py needs yaml — which is exactly the extra that venv
# carries and the service's deliberately does not.
{ pkgs, py }:
let
  venv = py.set.mkVirtualEnv "gotg-indexer-env" py.workspace.deps.optionals;
in
pkgs.runCommand "check-platforms" { nativeBuildInputs = [ venv ]; } ''
  export PYTHONPATH=${../../src}
  export GOTG_ENV_DIR=${../../src/client/env}
  python3 ${../../tests/indexer/check_platforms.py}
  touch $out
''
