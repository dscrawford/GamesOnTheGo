{
  lib,
  stdenvNoCC,
  makeWrapper,
  bash,
  curl,
  jq,
  unzip,
  gotg-pads,
  gnutar,
  zstd,
  zenity,
  coreutils,
  findutils,
  gnugrep,
  gnused,
  gawk,
  util-linux,
  nix,
}:

let
  # Everything the CLI shells out to. Named once, so the package and the dev
  # shell's run-from-the-checkout shim cannot drift into disagreeing about what
  # gotg needs on PATH — a drift that shows up as a command working when built
  # and failing when edited, or the reverse.
  runtimeInputs = [
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
    gotg-pads # reports what SDL sees, for generated bindings
    gnutar # save bundles
    zstd
    util-linux # flock, for the per-game download lock
    # `gotg install` builds emulators; launching never evaluates nix.
    nix
  ];
in
stdenvNoCC.mkDerivation {
  pname = "gotg";
  version = "0.1.0";

  passthru = { inherit runtimeInputs; };

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
      --prefix PATH : ${lib.makeBinPath runtimeInputs}

    runHook postInstall
  '';

  meta = {
    description = "Downloads games from the GOTG server on demand and launches them";
    mainProgram = "gotg";
  };
}
