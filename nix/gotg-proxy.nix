# The GOTG service, from the one workspace venv. deps.default carries no
# third-party packages at all — the internet-facing half stays stdlib-only,
# which is its whole supply-chain posture.
{
  runCommand,
  makeWrapper,
  venv,
}:

runCommand "gotg-proxy-0.5.0"
  {
    nativeBuildInputs = [ makeWrapper ];
    inherit venv;
    passthru.version = "0.5.0";
    meta = {
      description = "The GOTG service: catalog, streams, saves, and the artwork credentials";
      mainProgram = "gotg-proxy";
    };
  }
  ''
    mkdir -p $out/bin
    makeWrapper $venv/bin/gotg-proxy $out/bin/gotg-proxy
  ''
