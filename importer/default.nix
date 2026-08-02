{
  lib,
  python3Packages,
  makeWrapper,
  unrar,
  p7zip,
  dolphin-emu,
  zip,
  rhash,
  coreutils,
}:

python3Packages.buildPythonApplication {
  pname = "gotg-importer";
  version = "0.1.0";
  pyproject = true;

  src = lib.cleanSource ./.;

  build-system = [ python3Packages.setuptools ];

  dependencies = with python3Packages; [
    qbittorrent-api
    pyyaml
  ];

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  # Archive/checksum tools the handlers shell out to. Wrapped in rather than
  # assumed on PATH, so the container image needs no extra PATH wiring.
  postFixup = ''
    wrapProgram $out/bin/gotg-importer \
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
  '';

  meta = {
    description = "Organizes completed game torrents into the GOTG /Games tree";
    mainProgram = "gotg-importer";
  };
}
