# The Python suites. buildPythonApplication used to run these through
# pytestCheckHook; a uv2nix venv has no such hook, so they get a check of
# their own rather than quietly stopping.
{ pkgs, py }:
let
  # deps.all rather than deps.default: the dev group is where pytest is.
  venv = py.set.mkVirtualEnv "gotg-test-env" py.workspace.deps.all;
in
pkgs.runCommand "check-python-tests" { nativeBuildInputs = [ venv ]; } ''
  mkdir repo && cd repo
  cp -r ${../../tests} tests
  # The contract drift-guards read the shell client's patterns and the
  # project version; nothing else of src/client, so a client edit does
  # not re-run this suite.
  mkdir -p src/client/lib
  cp -r ${../../src/gotg} src/gotg
  # The picker's model half — catalog, paging, cursor — holds no
  # pygame on purpose, so it runs in this venv like anything else.
  # Its drawing does not, and is not tested here.
  cp -r ${../../src/ui} src/ui
  # The theme and settings the picker reads.
  cp -r ${../../config} config
  cp ${../../src/client/lib/common.sh} src/client/lib/common.sh
  # The browser player's cores are packaged from the list the service
  # reads (test_play), and /ports names a game's builds by the
  # environment files (test_ports).
  mkdir -p nix src/client/env
  cp ${../../nix/emulatorjs.nix} nix/emulatorjs.nix
  cp -r ${../../src/client/env/games} src/client/env/games
  cp ${../../pyproject.toml} pyproject.toml
  chmod -R u+w tests src config nix
  # And how the controller suite is split across the cluster's nodes: pure,
  # beside a suite that otherwise needs real devices.
  python -m pytest tests/service tests/indexer tests/ui tests/e2e/test_shards.py -q
  touch $out
''
