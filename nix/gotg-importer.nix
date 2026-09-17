# The indexer, from the same workspace venv as the service, plus the archive
# listers its classifier shells out to — wrapped in rather than assumed on
# PATH, so the container image needs no extra wiring.
{
  lib,
  runCommand,
  makeWrapper,
  venv,
  unrar,
  p7zip,
  coreutils,
}:

runCommand "gotg-importer-0.6.0"
  {
    nativeBuildInputs = [ makeWrapper ];
    inherit venv;
    # Bumped for --match. The image tag
    # comes from here, so leaving it would push over the tag the cluster is
    # currently running — the CronJob pins a digest, so nothing would break,
    # but the way back to the previous build would be gone.
    passthru.version = "0.6.0";
    meta = {
      description = "Indexes a folder of games into the GOTG catalog and /Games";
      mainProgram = "gotg-importer";
      license = lib.licenses.mit;
    };
  }
  ''
    mkdir -p $out/bin
    makeWrapper $venv/bin/gotg-importer $out/bin/gotg-importer \
      --prefix PATH : ${
        lib.makeBinPath [
          # Only to list what an archive holds — the client unpacks.
          unrar
          p7zip
          coreutils
        ]
      }
  ''
