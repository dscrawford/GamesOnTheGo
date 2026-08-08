# The importer, built from its own uv.lock rather than from a list of nixpkgs
# attributes kept in step by hand.
#
# `venv` is what uv2nix produced: a virtual environment holding gotg-importer
# and exactly the dependencies importer/uv.lock resolves to. All this adds is
# the part uv has no opinion about — the archive and checksum tools the handlers
# shell out to, wrapped in rather than assumed on PATH so the container image
# needs no extra wiring.
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

runCommand "gotg-importer-0.1.0"
  {
    nativeBuildInputs = [ makeWrapper ];
    inherit venv;

    # Read by the image builder and by anything else that wants to tag a build.
    passthru.version = "0.1.0";

    meta = {
      description = "Organizes completed game torrents into the GOTG /Games tree";
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
