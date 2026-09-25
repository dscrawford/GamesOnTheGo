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
  mkdir -p src/client/lib src/client/data
  cp -r ${../../src/gotg} src/gotg
  # The picker's model half — catalog, paging, cursor — holds no
  # pygame on purpose, so it runs in this venv like anything else.
  # Its drawing does not, and is not tested here.
  cp -r ${../../src/ui} src/ui
  # The controller descriptions the picker reads, and the tests check against
  # the artwork beside them.
  cp -r ${../../config} config
  cp ${../../src/client/lib/common.sh} src/client/lib/common.sh
  # And the ares binding table: the launch gate describes it, and one test
  # pins the N64 stick against the file both of them read.
  cp ${../../src/client/data/ares-pads.json} src/client/data/ares-pads.json
  cp ${../../pyproject.toml} pyproject.toml
  chmod -R u+w tests src config
  # And how the controller suite is split across the cluster's nodes: pure,
  # beside a suite that otherwise needs real devices.
  python -m pytest tests/service tests/indexer tests/ui tests/e2e/test_shards.py -q
  touch $out
''
