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
  # The controller descriptions the picker reads, and the tests check against
  # the artwork beside them.
  cp -r ${../../config} config
  cp ${../../src/client/lib/common.sh} src/client/lib/common.sh
  cp ${../../pyproject.toml} pyproject.toml
  chmod -R u+w tests src config
  python -m pytest tests/service tests/indexer tests/ui -q
  touch $out
''
