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
          # One registry of emulators, shared by the CLI and these outputs, so a
          # new emulator is a single JSON edit.
          emulators = builtins.fromJSON (builtins.readFile ./client/data/emulators.json);
          emulatorPackages = builtins.mapAttrs (_: spec: pkgs.${spec.attr}) emulators;
        in
        emulatorPackages
        // rec {
          gotg = pkgs.callPackage ./client { };
          gotg-importer = pkgs.callPackage ./importer { };
          default = gotg;

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

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            (python3.withPackages (ps: [
              ps.qbittorrent-api
              ps.pyyaml
              ps.pytest
            ]))
            ruff
            shellcheck
            bats
          ];
        };
      });

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
                unzip
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
