# A real bundle, end to end under Wine (windows-bundle.sh): the fixture
# library's gb.usa.tetris_2 zip, as `gotg port push --windows` builds it,
# taken by the server's port store, signed in, downloaded, its command line
# rendered, and its ares.exe running a game on Xvfb.
{
  pkgs,
  packages,
  flake,
}:

let
  library = flake.lib.mkLibrary {
    server = "https://gotg.example/";
    catalog = ../../tests/fixtures/catalog.json;
    library = ../../tests/fixtures;
  };
  bundle = library.legacyPackages.${pkgs.stdenv.hostPlatform.system}.gb.usa.tetris_2.gotgWindows;
in
pkgs.runCommand "check-windows-bundle"
  {
    nativeBuildInputs = [
      pkgs.wine64
      pkgs.xvfb-run
      pkgs.xwininfo
      pkgs.procps
      pkgs.unzip
      pkgs.jq
      pkgs.curl
      pkgs.python3
    ];
    passthru = { inherit bundle; };
  }
  ''
    export HOME=$TMPDIR/home
    mkdir -p $HOME
    xvfb-run -a -s "-screen 0 1280x720x24" bash ${./windows-bundle.sh} ${bundle} \
      ${packages.gotg-proxy}/bin/gotg-proxy ${pkgs.wine64}/bin/wine $TMPDIR/work
    touch $out
  ''
