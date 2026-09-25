{
  lib,
  stdenvNoCC,
  makeWrapper,
  # The flake this client was built from, in the store, lock and all: what
  # its emulator environments are built from when nobody named another.
  ownFlake ? null,
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

  # Bound here so the installPhase can substitute the same string the
  # derivation reports, and `gotg version` cannot drift from `nix eval`.
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
    chmod +x $out/share/gotg/steam/shortcuts.py
    chmod +x $out/share/gotg/qa/session.sh $out/share/gotg/qa/pad.py
    install -Dm644 completions/gotg.bash \
      $out/share/bash-completion/completions/gotg
    install -Dm755 bin/gotg $out/share/gotg/bin/gotg
    # `gotg version` reports this. Substituted rather than written into the
    # script so there is one place to bump, and --replace-fail so that place
    # going missing is a build error rather than a CLI that reports the
    # literal "@version@".
    substituteInPlace $out/share/gotg/bin/gotg \
      --replace-fail '@version@' '${version}'

    makeWrapper $out/share/gotg/bin/gotg $out/bin/gotg \
      --set GOTG_ROOT $out/share/gotg \
      ${lib.optionalString (ownFlake != null) "--set-default GOTG_OWN_FLAKE ${ownFlake} \\"}
      --prefix PATH : ${lib.makeBinPath runtimeInputs}

    runHook postInstall
  '';

  meta = {
    description = "Downloads games from the GOTG server on demand and launches them";
    mainProgram = "gotg";
    license = lib.licenses.mit;
  };
}
