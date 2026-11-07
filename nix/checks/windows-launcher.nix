# gotg.exe as Windows runs it, under Wine: --version, a dry run's command
# line and a whole launch against the real service (windows-launcher.sh);
# and the native build's integration tests against the same service
# (rust/crates/gotg-launch/tests/service.rs), which a plain `cargo test` skips.
{ pkgs, packages }:

let
  proxy = "${packages.gotg-proxy}/bin/gotg-proxy";
  native = packages.gotg-launch.overrideAttrs { GOTG_TEST_PROXY = proxy; };
in
pkgs.runCommand "check-windows-launcher"
  {
    nativeBuildInputs = [
      pkgs.wine64
      pkgs.jq
      pkgs.curl
      pkgs.python3
    ];
  }
  ''
    export HOME=$TMPDIR/home
    mkdir -p $HOME
    # The native tests ran as ${native} built.
    bash ${./windows-launcher.sh} ${packages.gotg-launch-windows}/bin/gotg.exe ${proxy} \
      ${pkgs.wine64}/bin/wine $TMPDIR/wine
    touch $out
  ''
