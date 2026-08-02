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

          # The whole privileged surface of controller support: one store path
          # holding Valve's udev rules. Everything else a controller needs is
          # unprivileged, so this is the only thing that has to touch the host —
          # either through nixosModules.controllers, or symlinked into
          # /etc/udev/rules.d by `gotg controllers install-rules`.
          #
          # Open question: whether to ship a GOTG-specific subset (28de:*,
          # 057e:0337, uinput) instead of the whole Valve file. Smaller thing to
          # audit, one more thing to keep current; the full package for now.
          controller-udev-rules = pkgs.steam-devices-udev-rules;

          # Asks the same library the emulators ask, so nothing downstream has
          # to guess which physical controller is which.
          gotg-pads = pkgs.callPackage ./client/gotg-pads { };

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

      # The NixOS way in, for hosts that have one. Everything here is host
      # configuration that a package cannot do for itself: udev rules, a kernel
      # module, an out-of-tree driver.
      nixosModules.controllers =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.programs.gotg.controllers;
        in
        {
          options.programs.gotg.controllers = {
            enable = lib.mkEnableOption "controller support for GOTG";

            xboxDongle = lib.mkEnableOption ''
              the xone driver, for the Xbox Wireless USB dongle.

              Off by default on purpose: xone blacklists xpad and mt76x2u, which
              changes how *wired* Xbox pads enumerate. Turn it on only if you
              actually have the dongle
            '';

            gcAdapterOverclock = lib.mkEnableOption ''
              1 ms polling for the Wii U GameCube adapter, instead of the
              stock 8 ms
            '';
          };

          config = lib.mkIf cfg.enable {
            # The same mechanism programs.steam.enable uses, so enabling both is
            # a no-op rather than a conflict.
            services.udev.packages = [ pkgs.steam-devices-udev-rules ];
            boot.kernelModules = [ "uinput" ];

            hardware.xone.enable = lib.mkIf cfg.xboxDongle true;
            boot.extraModulePackages = lib.mkIf cfg.gcAdapterOverclock [
              config.boot.kernelPackages.gcadapter-oc-kmod
            ];
          };
        };

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            # The wrapped CLI, so `gotg` is on PATH in the shell. It runs the
            # built copy under /nix/store, so rerun `nix develop` (or let direnv
            # reload) after editing client/.
            self.packages.${pkgs.stdenv.hostPlatform.system}.gotg
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
          ]);
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
