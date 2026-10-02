{
  lib,
  stdenvNoCC,
  makeWrapper,
  # The flake this client was built from, in the store, lock and all: what
  # its emulator environments are built from when nobody named another.
  bash,
  curl,
  jq,
  unzip,
  gotg-pads,
  danstick,
  danstick-rs,
  gotg-killswitch,
  gnutar,
  zstd,
  zenity,
  coreutils,
  findutils,
  gnugrep,
  gnused,
  gawk,
  util-linux,
  procps,
  python3,
  nix,
  mesa,
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
    procps # `gotg steam` has to know whether Steam is running
    (python3.withPackages (ps: [ ps.vdf ])) # binary shortcuts.vdf
    gotg-pads # reports what SDL sees, for generated bindings
    # The controller layer: `danstick ensure-daemon` before a launch, and
    # `danstick-rs exec` around it so the game is told about the pads it
    # publishes. SDL reads its database once, at startup, so a mapping has to
    # be in the environment before the emulator is.
    danstick
    danstick-rs
    gotg-killswitch # the controller way out of a running game
    gnutar # save bundles, and the archive a pull takes before it overwrites
    zstd
    util-linux # flock, for the per-game download lock
    # `gotg install` builds emulators; launching never evaluates nix.
    nix
  ];

  version = "0.1.0";
in
stdenvNoCC.mkDerivation {
  pname = "gotg";
  inherit version;

  passthru = { inherit runtimeInputs; };

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/gotg
    # env/ is shipped for its file names, not to be evaluated from here: the CLI
    # reads them to work out which flake attribute a game wants, without nix.
    cp -r lib data templates env steam qa $out/share/gotg/
    # The mesa the overlay this client starts points at on a machine without
    # GL of its own: named, and fetched there before a game (foreign-gl.sh).
    echo ${(import ./env/foreign-gl.nix { inherit mesa; }).path} >$out/share/gotg/foreign-gl
    chmod +x $out/share/gotg/steam/shortcuts.py
    chmod +x $out/share/gotg/qa/session.sh $out/share/gotg/qa/pad.py
    install -Dm644 completions/gotg.bash \
      $out/share/bash-completion/completions/gotg
    install -Dm755 bin/gotg $out/share/gotg/bin/gotg
    makeWrapper $out/share/gotg/bin/gotg $out/bin/gotg \
      --set GOTG_ROOT $out/share/gotg \
      --prefix PATH : ${lib.makeBinPath runtimeInputs}

    runHook postInstall
  '';

  meta = {
    description = "Downloads games from the GOTG server on demand and launches them";
    mainProgram = "gotg";
    license = lib.licenses.mit;
  };
}
