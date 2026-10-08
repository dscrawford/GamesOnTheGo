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
    # configuration file can pin player order to them: danstick asks the person
    # holding the controllers instead, and republishes each one through uinput
    # as a pad whose identity it made. Everything downstream binds to those.
    #
    # Pinned to a revision rather than following the branch, so that a launch
    # that worked yesterday is not changed by somebody else's commit today.
    danstick = {
      url = "github:dscrawford/danstick/d514477e5c7814f97d01a2dbf7d5e5379a44f535";
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
      danstick,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      # Emulators are unfree (ported game code), so import nixpkgs with
      # allowUnfree rather than using legacyPackages. The one import per
      # system: the packages, the checks, the dev shell and every library
      # built through mkLibrary all use this set.
      pkgsFor = nixpkgs.lib.genAttrs systems (
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        }
      );
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f pkgsFor.${system});
      systemOf = pkgs: pkgs.stdenv.hostPlatform.system;
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
      # What a library flake builds games with: the catalog as Nix data, the
      # environment each game runs in, and mkLibrary, which makes every game
      # in a catalog something `nix run` runs. See docs/nix-games.md.
      lib =
        let
          base = import ./lib { inherit (nixpkgs) lib; };
        in
        base
        // {
          mkLibrary = import ./nix/library.nix {
            inherit (nixpkgs) lib;
            pkgsFor = system: pkgsFor.${system};
            packagesFor = system: self.packages.${system};
            libraryLib = import ./lib/library.nix {
              inherit (nixpkgs) lib;
              catalogLib = base.catalog;
            };
            envDir = ./src/client/env;
          };
        };

      # programs.gotg, for home-manager: the picker and the games of a library
      # as packages. nix/modules/home-manager.nix.
      homeManagerModules.gotg = import ./nix/modules/home-manager.nix;
      homeManagerModules.default = self.homeManagerModules.gotg;

      # nix/packages.nix: the environments (one per platform and per game that
      # needs its own settings -- src/client/env) and everything else the flake
      # builds, as one set per system.
      packages = forAllSystems (
        pkgs:
        let
          system = systemOf pkgs;
        in
        import ./nix/packages.nix {
          inherit pkgs;
          danstickPkgs = danstick.packages.${system};
          py = pythonSets.${system};
        }
      );

      # `nix run github:dscrawford/GamesOnTheGo#install` -- the other way in,
      # for a machine that already has nix and wants the rest. And the
      # client's commands a person still runs, each its own app: nothing of
      # the client goes in a profile (docs/nix-games.md, phase 7).
      apps = forAllSystems (
        pkgs:
        let
          packages = self.packages.${systemOf pkgs};
          inherit (import ./nix/app.nix) mkApp shellApp;
          verb =
            name:
            shellApp pkgs {
              name = "gotg-${name}";
              text = ''exec ${packages.gotg}/bin/gotg ${name} "$@"'';
            };
          # A package's own binary, as the app.
          binApp = name: bin: mkApp "${packages.${name}}/bin/${bin}";
        in
        {
          login = verb "login";
          # A game with nothing of GOTG set up: src/client/play-anywhere.sh.
          play = shellApp pkgs {
            name = "gotg-play-anywhere";
            runtimeInputs = [
              pkgs.jq
              pkgs.nix
            ];
            text = ''
              GOTG_FLAKE="''${GOTG_FLAKE:-path:${self}}"
              ${builtins.readFile ./src/client/play-anywhere.sh}
            '';
          };
          admin = verb "admin";
          qa = verb "qa";
          controllers = verb "controllers";
          ui = binApp "gotg-ui" "gotg-ui";
          install = binApp "gotg-install" "gotg-install";
          uninstall = binApp "gotg-uninstall" "gotg-uninstall";
          # The controller requirement, on whatever machine is doubting it.
          # tests/e2e on the cluster, a pod per node: k8s/controllers/run.py.
          controllers-cluster = shellApp pkgs {
            name = "controllers-cluster";
            runtimeInputs = [
              pkgs.kubectl
              pkgs.skopeo
              pkgs.python3
            ];
            text = ''exec python3 ${./k8s/controllers/run.py} "$@"'';
          };
          test-controllers = binApp "gotg-test-controllers" "gotg-test-controllers";
        }
      );

      devShells = forAllSystems (
        pkgs:
        let
          system = systemOf pkgs;
          packages = self.packages.${system};
          gotgPkg = packages.gotg;
          devShim = import ./nix/dev-shim.nix { inherit pkgs; };

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
          gotg-dev = devShim {
            name = "gotg";
            marker = "src/client/bin/gotg";
            test = "-x";
            packaged = "gotg";
            body = ''
              export PATH="${pkgs.lib.makeBinPath gotgPkg.runtimeInputs}:$PATH"
              exec "$root/src/client/bin/gotg" "$@"
            '';
          };

          uiPython = pkgs.python3.withPackages (ps: [
            ps.pygame-ce
            ps.pyyaml
          ]);
          # What the packaged picker puts on PATH, and for the same reasons:
          # `gotg` because a pick execs it, and danstick because the picker is
          # what starts the daemon. Without danstick here the dev picker said
          # "danstick is not installed" at the top of the screen, found no
          # controllers however many were plugged in, and answered no hold --
          # a shell in which the one feature that needs a daemon cannot work.
          danstickPkg = danstick.packages.${system}.danstick;
          uiPath = pkgs.lib.makeBinPath [
            gotgPkg
            danstickPkg
            packages.gotg-killswitch
          ];

          # The picker, from the working tree, for the same reason `gotg` is.
          pickerDev = devShim {
            name = "gotg-ui";
            marker = "src/ui/gotg_ui";
            test = "-d";
            packaged = "gotg-ui";
            body = ''
              export PATH="${uiPath}:$PATH"
              export PYTHONPATH="$root/src/ui:${gotgPkg}/share/gotg/steam''${PYTHONPATH:+:$PYTHONPATH}"
              export GOTG_UI_ENV="''${GOTG_UI_ENV:-${gotgPkg}/share/gotg/env}"
              export GOTG_CONFIG="''${GOTG_CONFIG:-$root/config}"
              exec ${uiPython}/bin/python3 -m gotg_ui "$@"
            '';
          };
          controllerTests = packages.gotg-test-controllers;
        in
        {
          default = pkgs.mkShell {
            packages = [
              gotg-dev
              pickerDev
              # By hand as well as on the picker's PATH: `danstick list` is the
              # first question to ask when nothing joins, and
              # answering it should not mean digging the store path out of a
              # wrapper script.
              danstickPkg
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
              # The checkout's own client by name, not only by PATH: a pick
              # execs `gotg play`, and a launch that lost this PATH would run
              # an older one. Set only if unset so a test can still substitute
              # a recorder. The picker has no such variable, so it stays a
              # PATH entry.
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
          flake = self;
        }
      );

      # `nix flake init -t gotg#library`: a library flake for one server.
      templates.library = {
        path = ./templates/library;
        description = "A GOTG library: one server's games, each something `nix run` runs";
      };

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
