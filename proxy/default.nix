# The credential-holding proxy.
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
  version = "0.1.0";
  pyproject = true;
  src = lib.cleanSource ./.;

  build-system = [ python3.pkgs.setuptools ];

  nativeCheckInputs = [ python3.pkgs.pytest ];
  checkPhase = ''
    runHook preCheck
    python -m pytest tests/ -q
    runHook postCheck
  '';

  passthru.version = "0.1.0";

  meta = {
    description = "Holds the game-artwork API credentials so the clients do not";
    mainProgram = "gotg-proxy";
  };
}
