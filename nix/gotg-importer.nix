# The indexer, from the same workspace venv as the service, plus the archive
# and checksum tools its handlers shell out to — wrapped in rather than
# assumed on PATH, so the container image needs no extra wiring.
{
  lib,
  runCommand,
  makeWrapper,
  venv,
  unrar,
  p7zip,
  dolphin-emu,
  zip,
  rhash,
  coreutils,
}:

runCommand "gotg-importer-0.5.0"
  {
    nativeBuildInputs = [ makeWrapper ];
    inherit venv;
    passthru.version = "0.5.0";
    meta = {
      description = "Indexes completed game torrents into the GOTG catalog and /Games";
      mainProgram = "gotg-importer";
    };
  }
  ''
    mkdir -p $out/bin
    makeWrapper $venv/bin/gotg-importer $out/bin/gotg-importer \
      --prefix PATH : ${
        lib.makeBinPath [
          unrar
          p7zip
          # dolphin-tool: reads every disc format Dolphin plays and writes RVZ,
          # which is how a GameCube image is normalized on import (spec §5a).
          # A large closure for one binary, and the reason the image grew.
          dolphin-emu
          zip
          rhash
          coreutils
        ]
      }
  ''
