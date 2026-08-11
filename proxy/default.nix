# The GOTG service: the credential-holding proxy and the saves store.
#
# A plain buildPythonApplication rather than the uv2nix route the importer
# takes, because there is nothing to reproduce: this has no dependencies
# outside the standard library, on purpose. If that ever stops being true it
# should grow a uv.lock and move to the same builder as its sibling.
{
  python3,
  lib,
}:

python3.pkgs.buildPythonApplication {
  pname = "gotg-proxy";
  version = "0.2.0";
  pyproject = true;
  src = lib.cleanSource ./.;

  build-system = [ python3.pkgs.setuptools ];

  nativeCheckInputs = [ python3.pkgs.pytest ];
  checkPhase = ''
    runHook preCheck
    python -m pytest tests/ -q
    runHook postCheck
  '';

  passthru.version = "0.2.0";

  meta = {
    description = "The GOTG service: holds the artwork API credentials and the saves";
    mainProgram = "gotg-proxy";
  };
}
