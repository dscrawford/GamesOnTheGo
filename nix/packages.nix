# Every package the flake exposes for one system -- the environments, the
# client, the picker, the images -- as one set, for the flake's `packages` output
# and everything that needs "the flake's packages" (the library, the checks, the
# dev shell). The packages refer to each other here by name, not through `self`.
{
  pkgs,
  # danstick's packages for this system (the flake input's).
  danstickPkgs,
  # The uv2nix workspace and its Python set for this system.
  py,
}:
let
  inherit (import ./tools.nix { inherit pkgs; }) sdl3s tools;

  # Asks the same library the emulators ask, so nothing downstream has
  # to guess which physical controller is which.
  gotg-pads = pkgs.callPackage ../rust/gotg-pads.nix { sdl3 = sdl3s.gamepad; };

  # The controller's way out of a running game.
  gotg-killswitch = pkgs.callPackage ../rust/gotg-killswitch.nix {
    sdl3 = sdl3s.overlay;
    theme = ../config/theme.yaml;
    iconRules = ../config/icons.yaml;
    iconArt = ../src/ui/assets/icons;
    controllerArt = ../src/ui/assets/controllers;
    controllerConfig = ../config/controllers;
    font = "${pkgs.freefont_ttf}/share/fonts/truetype/FreeSansBold.ttf";
  };

  # One launchable environment per platform, plus one per game that needs
  # its own settings -- see src/client/env. `gotg play` builds these by name.
  envs = import ../src/client/env {
    inherit pkgs;
    # The same tools the packages below expose, so an environment names the
    # derivation the flake builds rather than a second evaluation of it.
    inherit tools;
    # For the split-screen sessions, which put the game inside danstick's
    # sandbox themselves -- see mods/four-swords-split.nix. `gotg-pads`
    # goes with it: that session has to ask what the *game* will see,
    # which is not what the session sees.
    inherit (danstickPkgs) danstick-rs;
    inherit gotg-pads;
  };
in
envs
// rec {
  gotg = pkgs.callPackage ../src/client {
    inherit gotg-pads gotg-killswitch;
    inherit (danstickPkgs) danstick danstick-rs;
  };

  # The picker. Takes the client rather than reimplementing it: what
  # makes a game run is already in src/client/lib and already tested,
  # and the copy nobody runs from a terminal is the one that rots.
  gotg-ui = pkgs.callPackage ../src/ui {
    inherit gotg gotg-killswitch;
    inherit (danstickPkgs) danstick;
  };
  # The controller requirement, run against a real danstick daemon and
  # real kernel devices: tests/e2e. Packaged rather than left in the
  # dev shell so the machine that matters can run it -- `nix run
  # .#test-controllers` on a Deck, over ssh, with no checkout to set
  # up first. Its own python because the picker's needs pygame and the
  # dev venv has none.
  gotg-test-controllers =
    let
      testPython = pkgs.python3.withPackages (ps: [
        ps.pygame-ce
        ps.pyyaml
        ps.pytest
      ]);
    in
    pkgs.writeShellScriptBin "gotg-test-controllers" ''
      root="''${GOTG_DEV_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
      if [ ! -d "$root/tests/e2e" ]; then
        echo "gotg-test-controllers: no tests/e2e under $root" >&2
        echo "      set GOTG_DEV_ROOT to your checkout" >&2
        exit 1
      fi
      export PATH="${
        pkgs.lib.makeBinPath [
          danstickPkgs.danstick
        ]
      }:$PATH"
      export PYTHONPATH="$root/src/ui''${PYTHONPATH:+:$PYTHONPATH}"
      export SDL_VIDEODRIVER="''${SDL_VIDEODRIVER:-dummy}"
      # Nothing here plays a sound; without this every test's
      # pygame.init() probed ALSA, and in a pod logged a screenful of
      # errors about it.
      export SDL_AUDIODRIVER="''${SDL_AUDIODRIVER:-dummy}"
      # Each test starts a danstick of its own under its tmp_path. This
      # is the one variable that could point it at the daemon somebody
      # is playing with instead.
      unset DANSTICK_SKIP_DAEMON_CHECK
      exec ${testPython}/bin/python3 -m pytest "$root/tests/e2e" "$@"
    '';

  # The installer, for a machine that has never heard of Nix. Packaged
  # as well as curl-able so that `gotg-install` is on PATH afterwards:
  # a SteamOS update wipes the udev rule, and re-running this is how it
  # comes back.
  gotg-install = pkgs.writeShellApplication {
    name = "gotg-install";
    runtimeInputs = with pkgs; [
      curl
      gnugrep
      procps # pgrep, to notice Steam is running
    ];
    text = builtins.readFile ../install.sh;
  };
  # Its inverse. Nix stays: the script says how to remove it instead.
  gotg-uninstall = pkgs.writeShellApplication {
    name = "gotg-uninstall";
    runtimeInputs = with pkgs; [ gnugrep ];
    text = builtins.readFile ../uninstall.sh;
  };

  # Both roles come from the one workspace: the indexer venv carries
  # the yaml extra, the service venv carries nothing at all.
  gotg-importer = pkgs.callPackage ./gotg-importer.nix {
    venv = py.set.mkVirtualEnv "gotg-indexer-env" py.workspace.deps.optionals;
  };
  gotg-proxy = pkgs.callPackage ./gotg-proxy.nix {
    venv = py.set.mkVirtualEnv "gotg-service-env" py.workspace.deps.default;
  };
  default = gotg;
  inherit gotg-pads gotg-killswitch;
  inherit (tools)
    dk64recomp
    melee-pc
    open-nectar
    battleship
    paperboat
    paperboat-torch
    snowboardkids2recomp
    wiimms-szs-tools
    pyisotools
    ;

  # What `gotg qa` runs a game inside: headless compositor, recorders,
  # analyzers, and a python that can create uinput pads. Built on
  # demand like the emulator environments.
  qa-tools = pkgs.callPackage ../src/client/qa/tools.nix { };

  # The QA runner as a container, for Jobs on the cluster.
  #
  #   nix build .#qa-image
  #   skopeo copy docker-archive:result docker://localhost:30500/gotg-qa:0.1.0
  qa-image = pkgs.callPackage ../src/client/qa/image.nix {
    inherit gotg qa-tools;
    # The cartridge platforms, which share one ares and so cost one
    # emulator between them, and the Switch — the one disc-era console
    # whose updates and DLC the harness needs to grade. The rest are
    # deliberately absent: each brings its own large emulator. One
    # decompiled port, so a native PC build of a game -- its own
    # window, its own renderer, no emulator -- is gradable too; the
    # overlay has to draw over all three kinds.
    environments = pkgs.lib.getAttrs [
      "env-gb"
      "env-gbc"
      "env-gba"
      "env-nes"
      "env-snes"
      "env-genesis"
      "env-n64"
      "env-n64-usa_super_mario_64-pc"
      "env-switch"
    ] envs;
  };

  # The controller suite as a container, for Jobs on the cluster.
  #
  #   nix build .#controllers-image
  #   skopeo copy docker-archive:result docker://localhost:30500/gotg-controllers:0.1.0
  #
  # `src` is the checkout, because a pod has no git tree and the suite
  # is what is under test: see tests/e2e/image.nix.
  controllers-image = pkgs.callPackage ../tests/e2e/image.nix {
    controllerTests = gotg-test-controllers;
    inherit (danstickPkgs) danstick;
    # Only what the suite reads. The whole checkout made every edit --
    # a doc, the bash client, a crate -- a new image, a new push and a
    # new tag to run, for tests that had not changed.
    src = pkgs.lib.fileset.toSource {
      root = ../.;
      fileset = pkgs.lib.fileset.unions [
        ../tests/e2e
        ../src/ui
        ../src/client/data
        ../config
      ];
    };
  };

  # Image for the in-cluster CronJob. The archive listers (unrar, 7z)
  # arrive through the wrapper's closure, so no extra PATH wiring is needed.
  #
  #   nix build .#importer-image
  #   skopeo copy docker-archive:result docker://localhost:30500/gotg-importer:0.1.0
  importer-image = pkgs.dockerTools.buildLayeredImage {
    name = "gotg-importer";
    tag = gotg-importer.passthru.version;
    contents = [
      gotg-importer
      pkgs.cacert
    ];
    config = {
      Entrypoint = [ (pkgs.lib.getExe gotg-importer) ];
      Env = [ "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt" ];
    };
  };

  # Image for the in-cluster GOTG service. cacert is not optional here:
  # every upstream it talks to is HTTPS, and a container with no trust
  # store fails every one of them at the handshake. The saves store
  # turns on when the deployment mounts a volume and points
  # GOTG_SAVES_DIR at it; without one the service is proxy-only.
  #
  #   nix build .#proxy-image
  #   skopeo copy docker-archive:result docker://localhost:30500/gotg-proxy:0.1.0
  proxy-image = pkgs.dockerTools.buildLayeredImage {
    name = "gotg-proxy";
    tag = gotg-proxy.passthru.version;
    contents = [
      gotg-proxy
      pkgs.cacert
    ];
    config = {
      Entrypoint = [ (pkgs.lib.getExe gotg-proxy) ];
      Env = [ "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt" ];
      ExposedPorts = {
        "8080/tcp" = { };
      };
      # Nothing here needs root, and a service holding every API
      # credential is the last place to hand it out.
      User = "65534:65534";
    };
  };
}
