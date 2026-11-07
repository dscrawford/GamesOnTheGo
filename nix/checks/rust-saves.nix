# gotg-saves against the real service and the real bash client: a Windows
# player's saves must be a Linux player's, so its integration tests push,
# pull and conflict through gotg-proxy and hand bundles to `gotg saves`.
# Also the Windows workspace's gate (rust/windows, apart from ../../rust so it
# never moves gotg-pads): rustfmt, clippy with warnings as errors.
{ pkgs, packages }:

let
  # The bash client as the integration tests run it: bin/gotg and its lib.
  client = pkgs.lib.fileset.toSource {
    root = ../../src/client;
    fileset = pkgs.lib.fileset.unions [
      ../../src/client/bin
      ../../src/client/lib
    ];
  };
in
pkgs.rustPlatform.buildRustPackage {
  pname = "check-rust-saves";
  version = "0.3.0";
  src = import ../../rust/windows/source.nix { inherit (pkgs) lib; };
  cargoLock.lockFile = ../../rust/windows/Cargo.lock;
  nativeBuildInputs = with pkgs; [
    clippy
    rustfmt
    packages.gotg-proxy
    bash
    jq
    curl
    gnutar
    zstd
    coreutils
    findutils
    gnugrep
    gnused
    gawk
    diffutils
    util-linux
  ];
  buildPhase = ''
    runHook preBuild
    cargo fmt --all --check
    cargo clippy --offline --all-targets -- -D warnings
    export HOME=$TMPDIR/home
    mkdir -p $HOME
    svc=$TMPDIR/service
    mkdir -p $svc/files $svc/library $svc/state
    port=18431
    GOTG_PROXY_TOKEN=test-token GOTG_SAVES_DIR=$svc/saves GOTG_FILES_DIR=$svc/files \
      GOTG_LIBRARY_ROOTS=$svc/library GOTG_CATALOG_DB=$svc/state/catalog.db \
      GOTG_INDEX_TOKEN=index-token GOTG_ART_DIR=$svc/art GOTG_LOGS_DIR=$svc/logs \
      GOTG_PORTS_DIR=$svc/ports GOTG_ADMIN_TOKEN=admin-token GOTG_LIBRARY_TOKEN=library-token \
      GOTG_TOKENS_DB=$svc/state/tokens.db PORT=$port \
      gotg-proxy >$svc/log 2>&1 &
    pid=$!
    for _ in $(seq 1 100); do
      curl -s -o /dev/null http://127.0.0.1:$port/healthz && break
      kill -0 $pid || { cat $svc/log; exit 1; }
      sleep 0.1
    done
    export GOTG_SAVES_REQUIRE=1 GOTG_SAVES_SERVICE=http://127.0.0.1:$port GOTG_SAVES_CLIENT=${client}
    cargo test --offline -p gotg-saves || { cat $svc/log; exit 1; }
    kill $pid
    runHook postBuild
  '';
  doCheck = false;
  installPhase = "touch $out";
}
