{
  description = "GamesOnTheGo — on-demand game downloader, importer and emulator launcher";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # The importer is a uv project: its dependencies are resolved and hashed in
    # importer/uv.lock, and uv2nix builds them straight from that. `uv lock` and
    # `nix build` therefore agree by construction, rather than by someone
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
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
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
      # The importer's dependency set, straight out of importer/uv.lock.
      #
      # sourcePreference = "wheel": these are pure-python packages published as
      # wheels, so building from sdists would only add work and a build-system
      # to resolve for each. Anything needing a compiler would want "sdist".
      pythonSets = forAllSystems (
        pkgs:
        let
          workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./importer; };
          overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
        in
        {
          inherit workspace;
          set =
            (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python312; }).overrideScope
              (
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
          # its own settings — see client/env. `gotg play` builds these by name.
          envs = import ./client/env { inherit pkgs; };
          py = pythonSets.${pkgs.stdenv.hostPlatform.system};
        in
        envs
        // rec {
          gotg = pkgs.callPackage ./client { inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg-pads; };
          gotg-importer = pkgs.callPackage ./importer {
            venv = py.set.mkVirtualEnv "gotg-importer-env" py.workspace.deps.default;
          };
          gotg-proxy = pkgs.callPackage ./proxy { };
          default = gotg;


          # Asks the same library the emulators ask, so nothing downstream has
          # to guess which physical controller is which.
          gotg-pads = pkgs.callPackage ./client/gotg-pads { };

          # Not in nixpkgs, though its sibling wiimms-iso-tools is. Needed to
          # open and rebuild the Yaz0 archives GameCube games keep their data in.
          wiimms-szs-tools = pkgs.callPackage ./pkgs/wiimms-szs-tools { };

          # Extracts and rebuilds GameCube discs. wiimms-iso-tools can do the
          # first but rebuilds as a Wii disc, which boots into nothing.
          pyisotools = pkgs.callPackage ./pkgs/pyisotools { };

          # Image for the in-cluster CronJob. The archive tools (unrar, zip, rhash)
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

          # Image for the in-cluster API proxy. cacert is not optional here:
          # every upstream it talks to is HTTPS, and a container with no trust
          # store fails every one of them at the handshake.
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
              ExposedPorts = { "8080/tcp" = { }; };
              # Nothing here needs root, and a service holding every API
              # credential is the last place to hand it out.
              User = "65534:65534";
            };
          };
        }
      );

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
            if [ ! -x "$root/client/bin/gotg" ]; then
              echo "gotg: no client/bin/gotg under $root" >&2
              echo "      set GOTG_DEV_ROOT to your checkout, or use: nix run .#gotg" >&2
              exit 1
            fi
            export PATH="${pkgs.lib.makeBinPath gotgPkg.runtimeInputs}:$PATH"
            exec "$root/client/bin/gotg" "$@"
          '';
        in
        {
        default = pkgs.mkShell {
          packages = [
            gotg-dev
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
              py.set.mkVirtualEnv "gotg-importer-dev-env" py.workspace.deps.all
            )
            pkgs.uv
          ]
          ++ (with pkgs; [
            ruff
            shellcheck
            bats
            git
          ]);

          # Pin the checkout at shell entry, so `gotg` keeps meaning this tree
          # even from a subdirectory.
          shellHook = ''
            export GOTG_DEV_ROOT="$PWD"
            # The completion out of the checkout, so a change to it applies on
            # save like everything else here. Guarded because the builtins it
            # uses exist only where bash was built with programmable completion.
            if [ -n "''${BASH_VERSION:-}" ] && type -t complete >/dev/null 2>&1; then
              . "$PWD/client/completions/gotg.bash"
            fi
          '';
        };
        }
      );

      checks = forAllSystems (pkgs: {
        importer = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-importer;

        # The proxy runs its own tests in its checkPhase, being an ordinary
        # buildPythonApplication rather than a uv2nix venv.
        proxy = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-proxy;

        # buildPythonApplication used to run these through pytestCheckHook; a
        # uv2nix venv has no such hook, so they get a check of their own rather
        # than quietly stopping.
        importer-tests =
          let
            py = pythonSets.${pkgs.stdenv.hostPlatform.system};
            # deps.all rather than deps.default: the dev group is where pytest is.
            venv = py.set.mkVirtualEnv "gotg-importer-test-env" py.workspace.deps.all;
          in
          pkgs.runCommand "check-importer-tests" { nativeBuildInputs = [ venv ]; } ''
            cp -r ${./importer} importer
            chmod -R u+w importer
            cd importer
            python -m pytest tests/
            touch $out
          '';
        client = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg;

        shellcheck =
          pkgs.runCommand "check-shellcheck"
            {
              nativeBuildInputs = [ pkgs.shellcheck ];
            }
            ''
              cd ${./.}
              shellcheck --external-sources --source-path=client client/bin/gotg client/lib/*.sh
              touch $out
            '';

        # End-to-end against a stand-in File Browser: real HTTP, real resume,
        # real checksums, no network.
        client-tests =
          pkgs.runCommand "check-client-tests"
            {
              nativeBuildInputs = with pkgs; [
                bats
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
              ];
              GOTG_BIN = pkgs.lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.gotg;
            }
            ''
              cp -r ${./client/tests} tests
              chmod -R u+w tests
              export HOME=$TMPDIR
              bats --print-output-on-failure tests/
              touch $out
            '';

        ruff =
          pkgs.runCommand "check-ruff"
            {
              nativeBuildInputs = [ pkgs.ruff ];
            }
            ''
              cd ${./.}
              ruff check --no-cache importer
              ruff format --no-cache --check importer
              touch $out
            '';
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
