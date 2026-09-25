{
  description = "GamesOnTheGo — on-demand game downloader, importer and emulator launcher";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # The Python package is a uv project: its dependencies are resolved and
    # hashed in uv.lock, and uv2nix builds them straight from that. `uv lock`
    # and `nix build` therefore agree by construction, rather than by someone
    # remembering to update a list of nixpkgs attributes to match.
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # The controller layer. Four identical adapter ports report the same
    # everything and differ only by an ordinal libudev sorts as a string, so no
    # configuration file can pin player order to them: padmap asks the person
    # holding the controllers instead, and republishes each one through uinput
    # as a pad whose identity it made. Everything downstream binds to those.
    #
    # Pinned to a revision rather than following the branch, so that a launch
    # that worked yesterday is not changed by somebody else's commit today.
    padmap = {
      url = "git+ssh://git@github.com/dscrawford/padmap?ref=main&rev=b81d476a395354f18fdb5ae30b8e1e721f4b4038";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      padmap,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      # Emulators are unfree (ported game code), so import nixpkgs with
      # allowUnfree rather than using legacyPackages.
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          f (
            import nixpkgs {
              inherit system;
              config.allowUnfree = true;
            }
          )
        );
      # The one Python workspace — service and indexer are roles of a single
      # package, resolved from the root uv.lock.
      #
      # sourcePreference = "wheel": these are pure-python packages published as
      # wheels, so building from sdists would only add work and a build-system
      # to resolve for each. Anything needing a compiler would want "sdist".
      pythonSets = forAllSystems (
        pkgs:
        let
          # Only what the wheel actually packages: a docs or client commit must
          # not rebuild the venvs (and re-run every check downstream of them).
          workspace = uv2nix.lib.workspace.loadWorkspace {
            workspaceRoot = nixpkgs.lib.fileset.toSource {
              root = ./.;
              fileset = nixpkgs.lib.fileset.unions [
                ./pyproject.toml
                ./uv.lock
                ./src/gotg
              ];
            };
          };
          overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
        in
        {
          inherit workspace;
          set = (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python312; }).overrideScope (
            nixpkgs.lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              overlay
            ]
          );
        }
      );
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          # One launchable environment per platform, plus one per game that needs
          # its own settings — see src/client/env. `gotg play` builds these by name.
          envs = import ./src/client/env {
            inherit pkgs;
            # For the split-screen sessions, which put the game inside padmap's
            # sandbox themselves -- see mods/four-swords-split.nix. `gotg-pads`
            # goes with it: that session has to ask what the *game* will see,
            # which is not what the session sees.
            inherit (padmap.packages.${pkgs.stdenv.hostPlatform.system}) padmap-rs;
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg-pads;
          };
          py = pythonSets.${pkgs.stdenv.hostPlatform.system};
        in
        envs
        // rec {
          gotg = pkgs.callPackage ./src/client {
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg-pads gotg-killswitch;
            inherit (padmap.packages.${pkgs.stdenv.hostPlatform.system}) padmap padmap-rs;
          };

          # The picker. Takes the client rather than reimplementing it: what
          # makes a game run is already in src/client/lib and already tested,
          # and the copy nobody runs from a terminal is the one that rots.
          gotg-ui = pkgs.callPackage ./src/ui {
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg;
            inherit (padmap.packages.${pkgs.stdenv.hostPlatform.system}) padmap;
          };
          # The controller requirement, run against a real padmap daemon and
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
              export PATH="${pkgs.lib.makeBinPath [
                padmap.packages.${pkgs.stdenv.hostPlatform.system}.padmap
              ]}:$PATH"
              export PYTHONPATH="$root/src/ui''${PYTHONPATH:+:$PYTHONPATH}"
              export SDL_VIDEODRIVER="''${SDL_VIDEODRIVER:-dummy}"
              # Nothing here plays a sound; without this every test's
              # pygame.init() probed ALSA, and in a pod logged a screenful of
              # errors about it.
              export SDL_AUDIODRIVER="''${SDL_AUDIODRIVER:-dummy}"
              # Each test starts a padmap of its own under its tmp_path. This
              # is the one variable that could point it at the daemon somebody
              # is playing with instead.
              unset PADMAP_SKIP_DAEMON_CHECK
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
            text = builtins.readFile ./install.sh;
          };
          # Its inverse. Nix stays: the script says how to remove it instead.
          gotg-uninstall = pkgs.writeShellApplication {
            name = "gotg-uninstall";
            runtimeInputs = with pkgs; [ gnugrep ];
            text = builtins.readFile ./uninstall.sh;
          };

          # Both roles come from the one workspace: the indexer venv carries
          # the yaml extra, the service venv carries nothing at all.
          gotg-importer = pkgs.callPackage ./nix/gotg-importer.nix {
            venv = py.set.mkVirtualEnv "gotg-indexer-env" py.workspace.deps.optionals;
          };
          gotg-proxy = pkgs.callPackage ./nix/gotg-proxy.nix {
            venv = py.set.mkVirtualEnv "gotg-service-env" py.workspace.deps.default;
          };
          default = gotg;

          # Asks the same library the emulators ask, so nothing downstream has
          # to guess which physical controller is which.
          gotg-pads = pkgs.callPackage ./rust/gotg-pads.nix { };

          # The controller's way out of a running game.
          gotg-killswitch = pkgs.callPackage ./rust/gotg-killswitch.nix {
            theme = ./config/theme.yaml;
            iconRules = ./config/icons.yaml;
            iconArt = ./src/ui/assets/icons;
            controllerArt = ./src/ui/assets/controllers;
            controllerConfig = ./config/controllers;
          };

          # What `gotg qa` runs a game inside: headless compositor, recorders,
          # analyzers, and a python that can create uinput pads. Built on
          # demand like the emulator environments.
          qa-tools = pkgs.callPackage ./src/client/qa/tools.nix { };

          # The QA runner as a container, for Jobs on the cluster.
          #
          #   nix build .#qa-image
          #   skopeo copy docker-archive:result docker://localhost:30500/gotg-qa:0.1.0
          qa-image = pkgs.callPackage ./src/client/qa/image.nix {
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg qa-tools;
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
          controllers-image = pkgs.callPackage ./tests/e2e/image.nix {
            controllerTests = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-test-controllers;
            padmap = padmap.packages.${pkgs.stdenv.hostPlatform.system}.padmap;
            # Only what the suite reads. The whole checkout made every edit --
            # a doc, the bash client, a crate -- a new image, a new push and a
            # new tag to run, for tests that had not changed.
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [
                ./tests/e2e
                ./src/ui
                ./src/client/data
                ./config
              ];
            };
          };

          # Donkey Kong 64: Recompiled — not in nixpkgs, though its siblings
          # zelda64recomp and n64recomp are.
          dk64recomp = pkgs.callPackage ./pkgs/dk64recomp { };

          # Super Smash Bros. Melee — the native port off doldecomp/melee.
          melee-pc = pkgs.callPackage ./pkgs/melee-pc { };

          # Pikmin — the native port off the projectPiki decompilation.
          open-nectar = pkgs.callPackage ./pkgs/open-nectar { };

          # Super Smash Bros. (N64) — the libultraship port, not a recomp.
          battleship = pkgs.callPackage ./pkgs/battleship { };

          # Snowboard Kids 2: Recompiled — the same N64Recomp/RT64 stack as
          # dk64recomp above, and likewise not in nixpkgs.
          snowboardkids2recomp = pkgs.callPackage ./pkgs/snowboardkids2recomp { };

          # Not in nixpkgs, though its sibling wiimms-iso-tools is. Needed to
          # open and rebuild the Yaz0 archives GameCube games keep their data in.
          wiimms-szs-tools = pkgs.callPackage ./pkgs/wiimms-szs-tools { };

          # Extracts and rebuilds GameCube discs. wiimms-iso-tools can do the
          # first but rebuilds as a Wii disc, which boots into nothing.
          pyisotools = pkgs.callPackage ./pkgs/pyisotools { };

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
      );

      # `nix run github:dscrawford/GamesOnTheGo#install` -- the other way in,
      # for a machine that already has nix and wants the rest.
      apps = forAllSystems (pkgs: {
        install = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-install}/bin/gotg-install";
        };
        uninstall = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-uninstall}/bin/gotg-uninstall";
        };
        # The controller requirement, on whatever machine is doubting it.
        # tests/e2e on the cluster, a pod per node: k8s/controllers/run.py.
        controllers-cluster = {
          type = "app";
          program = "${
            pkgs.writeShellApplication {
              name = "controllers-cluster";
              runtimeInputs = [
                pkgs.kubectl
                pkgs.skopeo
                pkgs.python3
              ];
              text = ''exec python3 ${./k8s/controllers/run.py} "$@"'';
            }
          }/bin/controllers-cluster";
        };
        test-controllers = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-test-controllers}/bin/gotg-test-controllers";
        };
      });

      devShells = forAllSystems (
        pkgs:
        let
          gotgPkg = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg;

          # `gotg` in the dev shell runs the working tree, not the store.
          #
          # The packaged wrapper sets GOTG_ROOT to its own copy under /nix/store,
          # so editing client/ did nothing until the shell was re-entered — and
          # a flake only sees git-tracked files, so a brand-new lib/*.sh did
          # nothing even then, until it was added. Both are a poor way to find
          # out you have been testing yesterday's code.
          #
          # This takes its PATH from the package's own runtimeInputs, so the two
          # cannot disagree about what gotg needs, and execs the checkout. Edits
          # apply on save, with nothing to rebuild. `nix run .#gotg` is still
          # there when what you want is the packaged article.
          gotg-dev = pkgs.writeShellScriptBin "gotg" ''
            root="''${GOTG_DEV_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
            if [ ! -x "$root/src/client/bin/gotg" ]; then
              echo "gotg: no src/client/bin/gotg under $root" >&2
              echo "      set GOTG_DEV_ROOT to your checkout, or use: nix run .#gotg" >&2
              exit 1
            fi
            export PATH="${pkgs.lib.makeBinPath gotgPkg.runtimeInputs}:$PATH"
            exec "$root/src/client/bin/gotg" "$@"
          '';

          uiPkg = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-ui;
          uiPython = pkgs.python3.withPackages (ps: [ ps.pygame-ce ps.pyyaml ]);
          # What the packaged picker puts on PATH, and for the same reasons:
          # `gotg` because a pick execs it, and padmap because the picker is
          # what starts the daemon. Without padmap here the dev picker said
          # "padmap is not installed" at the top of the screen, found no
          # controllers however many were plugged in, and answered no hold --
          # a shell in which the one feature that needs a daemon cannot work.
          padmapPkg = padmap.packages.${pkgs.stdenv.hostPlatform.system}.padmap;
          uiPath = pkgs.lib.makeBinPath [
            gotgPkg
            padmapPkg
          ];

          # The picker and the controller check, from the working tree, for the
          # same reason `gotg` is. These two especially: the check runs in front
          # of a launch and is found *beside the picker*, so a shell with one
          # and not the other tests a path that cannot happen on a real machine
          # -- which is exactly how it came to be shipped skipping itself.
          #
          # Only the artwork comes from the store. It is resvg output, produced
          # at build time from the SVGs, and rasterising it on shell entry would
          # charge every `nix develop` for something that changes about twice a
          # year.
          # The check first, so the picker can point at it. In the packaged
          # install the two sit in one bin/ and the client finds the check
          # beside the picker; here each shim is its own store path, so a
          # picker started from Steam launched games past the check with
          # "no gotg-seat here" in a log nobody reads, and Ryujinx then asked
          # for a controller itself. GOTG_SEAT is the client's explicit
          # override, and the picker sets it on its way in.
          uiDev = name: module: extra: pkgs.writeShellScriptBin name ''
            root="''${GOTG_DEV_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
            if [ ! -d "$root/src/ui/gotg_ui" ]; then
              echo "${name}: no src/ui/gotg_ui under $root" >&2
              echo "      set GOTG_DEV_ROOT to your checkout, or use: nix run .#gotg-ui" >&2
              exit 1
            fi
            export PATH="${uiPath}:$PATH"
            export PYTHONPATH="$root/src/ui:${gotgPkg}/share/gotg/steam''${PYTHONPATH:+:$PYTHONPATH}"
            export GOTG_UI_DATA="''${GOTG_UI_DATA:-${gotgPkg}/share/gotg/data}"
            export GOTG_UI_ENV="''${GOTG_UI_ENV:-${gotgPkg}/share/gotg/env}"
            export GOTG_UI_ASSETS="''${GOTG_UI_ASSETS:-${uiPkg}/share/gotg-ui/assets}"
            export GOTG_CONFIG="''${GOTG_CONFIG:-$root/config}"
            ${extra}
            exec ${uiPython}/bin/python3 -m ${module} "$@"
          '';
          seatDev = uiDev "gotg-seat" "gotg_ui.seat" "";

          controllerTests = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-test-controllers;
          pickerDev = uiDev "gotg-ui" "gotg_ui" ''
            export GOTG_SEAT="''${GOTG_SEAT:-${seatDev}/bin/gotg-seat}"
          '';
        in
        {
          default = pkgs.mkShell {
            packages = [
              gotg-dev
              pickerDev
              seatDev
              # By hand as well as on the picker's PATH: `padmap list` is the
              # first question to ask when the strip says no controllers, and
              # answering it should not mean digging the store path out of a
              # wrapper script.
              padmapPkg
              controllerTests
            ]
            ++ [
              # The importer's own dependencies, out of its lock rather than a
              # second list that drifts from it. `uv` is here to edit that lock —
              # `uv lock`, `uv add` — after which `nix build` picks the change up
              # with nothing else to update.
              (
                let
                  py = pythonSets.${pkgs.stdenv.hostPlatform.system};
                in
                py.set.mkVirtualEnv "gotg-dev-env" py.workspace.deps.all
              )
              pkgs.uv
            ]
            ++ (with pkgs; [
              ruff
              shellcheck
              bats
              git
              bash-completion
            ]);

            # Pin the checkout at shell entry, so `gotg` keeps meaning this tree
            # even from a subdirectory.
            shellHook = ''
              export GOTG_DEV_ROOT="$PWD"
              # The checkout's own gate and client by name, not only by PATH:
              # a pick execs `gotg play`, which looks for `gotg-seat`, and a
              # launch that lost this PATH found an older one that had no
              # ready-up door and started the game on the seating hold. These
              # are the client's and picker's existing overrides, set only if
              # unset so a test can still substitute a recorder. The picker
              # has no such variable, so it stays a PATH entry.
              export GOTG_SEAT="''${GOTG_SEAT:-${seatDev}/bin/gotg-seat}"
              export GOTG_BIN="''${GOTG_BIN:-${gotg-dev}/bin/gotg}"
              export PATH="${pickerDev}/bin:$PATH"
              # Machinery first, then gotg's own from the checkout so edits
              # apply on save. `nix develop` only — direnv shells get theirs
              # via the XDG_DATA_DIRS publish in .envrc.
              if [ -n "''${BASH_VERSION:-}" ] && type -t complete >/dev/null 2>&1; then
                . ${pkgs.bash-completion}/etc/profile.d/bash_completion.sh
                . "$PWD/src/client/completions/gotg.bash"
              fi
            '';
          };
        }
      );

      # One file per check, under nix/checks — see the comment there.
      checks = forAllSystems (
        pkgs:
        import ./nix/checks {
          inherit pkgs;
          packages = self.packages.${pkgs.stdenv.hostPlatform.system};
          py = pythonSets.${pkgs.stdenv.hostPlatform.system};
        }
      );

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
