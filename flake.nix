{
  description = "GamesOnTheGo — on-demand game downloader, importer and emulator launcher";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
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
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          # One launchable environment per platform, plus one per game that needs
          # its own settings — see client/env. `gotg play` builds these by name.
          envs = import ./client/env { inherit pkgs; };
        in
        envs
        // rec {
          gotg = pkgs.callPackage ./client { inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg-pads; };
          gotg-importer = pkgs.callPackage ./importer { };
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
            tag = gotg-importer.version;
            contents = [
              gotg-importer
              pkgs.cacert
            ];
            config = {
              Entrypoint = [ (pkgs.lib.getExe gotg-importer) ];
              Env = [ "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt" ];
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
          ++ (with pkgs; [
            (python3.withPackages (ps: [
              ps.qbittorrent-api
              ps.pyyaml
              ps.pytest
            ]))
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
                python3
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
              bats tests/
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
