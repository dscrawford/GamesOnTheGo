{
  lib,
  stdenvNoCC,
  makeWrapper,
  bash,
  curl,
  jq,
  unzip,
  zenity,
  coreutils,
  findutils,
  gnugrep,
  gnused,
  gawk,
  util-linux,
  nix,
}:

stdenvNoCC.mkDerivation {
  pname = "gotg";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/gotg
    # env/ is shipped for its file names, not to be evaluated from here: the CLI
    # reads them to work out which flake attribute a game wants, without nix.
    cp -r lib data templates env $out/share/gotg/
    install -Dm755 bin/gotg $out/share/gotg/bin/gotg

    makeWrapper $out/share/gotg/bin/gotg $out/bin/gotg \
      --set GOTG_ROOT $out/share/gotg \
      --prefix PATH : ${
        lib.makeBinPath [
          bash
          curl
          jq
          unzip
          zenity
          coreutils
          findutils
          gnugrep
          gnused
          gawk
          util-linux # flock, for the per-game download lock
          # `gotg install` builds emulators; launching never evaluates nix.
          nix
        ]
      }

    runHook postInstall
  '';

  meta = {
    description = "Downloads games from the GOTG server on demand and launches them";
    mainProgram = "gotg";
  };
}
