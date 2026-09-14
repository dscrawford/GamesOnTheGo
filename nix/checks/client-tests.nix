# End-to-end against a stand-in File Browser — real HTTP, real resume, real
# checksums, no network — and against the real GOTG service for saves, since
# its conflict rules are the thing under test.
{ pkgs, packages }:
pkgs.runCommand "check-client-tests"
  {
    nativeBuildInputs = with pkgs; [
      bats
      parallel # bats --jobs runs test files concurrently through it
      jq
      curl
      (python3.withPackages (ps: [ ps.vdf ]))
      coreutils
      zip
      unzip
      gnutar
      zstd
      gnugrep
      gnused
      gawk
      diffutils
      util-linux # flock: firmware_ensure serializes on the platform cache
      gitMinimal # sync fingerprints a checkout by its commit
      ffmpeg # qa.bats synthesizes its fixtures and runs the analyzers
      imagemagick # qa.bats: the golden-frame phash comparison
      packages.gotg-proxy
      # The watcher itself, so its own process handling — a target already
      # gone, a target that exits while it waits — is tested against the real
      # binary rather than a stand-in.
      packages.gotg-killswitch
    ];
    GOTG_BIN = pkgs.lib.getExe packages.gotg;
  }
  ''
    cp -r ${../../tests/client} tests
    chmod -R u+w tests
    export HOME=$TMPDIR
    # Every test isolates under its own BATS_TEST_TMPDIR and picks
    # random ports, which is what makes running them concurrently
    # sound. Bounded rather than nproc: each test can boot a real
    # gotg-proxy, and the build sandbox shares the machine.
    bats --print-output-on-failure --jobs 8 tests/
    touch $out
  ''
