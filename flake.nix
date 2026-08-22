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
          # its own settings — see src/client/env. `gotg play` builds these by name.
          envs = import ./src/client/env { inherit pkgs; };
          py = pythonSets.${pkgs.stdenv.hostPlatform.system};
        in
        envs
        // rec {
          gotg = pkgs.callPackage ./src/client { inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg-pads; };

          # The picker. Takes the client rather than reimplementing it: what
          # makes a game run is already in src/client/lib and already tested,
          # and the copy nobody runs from a terminal is the one that rots.
          gotg-ui = pkgs.callPackage ./src/ui {
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) gotg;
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
          gotg-pads = pkgs.callPackage ./src/client/gotg-pads { };

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
            if [ ! -x "$root/src/client/bin/gotg" ]; then
              echo "gotg: no src/client/bin/gotg under $root" >&2
              echo "      set GOTG_DEV_ROOT to your checkout, or use: nix run .#gotg" >&2
              exit 1
            fi
            export PATH="${pkgs.lib.makeBinPath gotgPkg.runtimeInputs}:$PATH"
            exec "$root/src/client/bin/gotg" "$@"
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

      checks = forAllSystems (pkgs: {
        importer = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-importer;

        # Building the wrapper runs no tests (unlike the old
        # buildPythonApplication checkPhase); python-tests below is the gate.
        proxy = self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-proxy;

        # buildPythonApplication used to run these through pytestCheckHook; a
        # uv2nix venv has no such hook, so they get a check of their own rather
        # than quietly stopping.
        python-tests =
          let
            py = pythonSets.${pkgs.stdenv.hostPlatform.system};
            # deps.all rather than deps.default: the dev group is where pytest is.
            venv = py.set.mkVirtualEnv "gotg-test-env" py.workspace.deps.all;
          in
          pkgs.runCommand "check-python-tests" { nativeBuildInputs = [ venv ]; } ''
            mkdir repo && cd repo
            cp -r ${./tests} tests
            # The contract drift-guards read the shell client's patterns and the
            # project version; nothing else of src/client, so a client edit does
            # not re-run this suite.
            mkdir -p src/client/lib
            cp -r ${./src/gotg} src/gotg
            # The picker's model half — catalog, paging, cursor — holds no
            # pygame on purpose, so it runs in this venv like anything else.
            # Its drawing does not, and is not tested here.
            cp -r ${./src/ui} src/ui
            cp ${./src/client/lib/common.sh} src/client/lib/common.sh
            cp ${./pyproject.toml} pyproject.toml
            chmod -R u+w tests src
            python -m pytest tests/service tests/indexer tests/ui -q
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
              shellcheck --external-sources --source-path=src/client src/client/bin/gotg src/client/lib/*.sh
              touch $out
            '';

        # End-to-end against a stand-in File Browser — real HTTP, real resume,
        # real checksums, no network — and against the real GOTG service for
        # saves, since its conflict rules are the thing under test.
        client-tests =
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
                self.packages.${pkgs.stdenv.hostPlatform.system}.gotg-proxy
              ];
              GOTG_BIN = pkgs.lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.gotg;
            }
            ''
              cp -r ${./tests/client} tests
              chmod -R u+w tests
              export HOME=$TMPDIR
              # Every test isolates under its own BATS_TEST_TMPDIR and picks
              # random ports, which is what makes running them concurrently
              # sound. Bounded rather than nproc: each test can boot a real
              # gotg-proxy, and the build sandbox shares the machine.
              bats --print-output-on-failure --jobs 8 tests/
              touch $out
            '';

        # The recipe chains, with the heavy tools stubbed: what is asserted is
        # the plumbing — sfv gate, staging, largest-file selection, conversion
        # arguments, cleanup — not a real unrar or a real disc conversion.
        recipes =
          let
            switch = self.packages.${pkgs.stdenv.hostPlatform.system}.env-switch;
            gamecube = self.packages.${pkgs.stdenv.hostPlatform.system}.env-gamecube;
            # The real harkinian envs are too heavy to build in a check (they
            # carry the ports); a dummy port pins the helper's contract at
            # eval time instead. Both handlers, because the same zip is
            # single_file from the games-root pass and no_intro_set from the
            # DAT torrent path.
            harkinianProbe =
              (import ./src/client/env/helpers.nix {
                inherit pkgs;
                inherit (pkgs) lib;
              }).harkinianPort
                {
                  port = pkgs.coreutils;
                  bin = "true";
                  appName = "probe";
                  archives = [ ];
                };
            # An environment nothing ships, composing its own pipeline from the
            # step library — the check that steps are usable à la carte, not
            # only through the two canned recipes.
            probe =
              (import ./src/client/env/lib.nix {
                inherit pkgs;
                inherit (pkgs) lib;
              })
                {
                  name = "recipe-probe";
                  emulator = pkgs.coreutils;
                  bin = "true";
                  # One glob of each shape: a directory tree, and a file
                  # pattern whose wildcard segment must not become a mkdir.
                  saves = [
                    "saves/**"
                    "data/probe/probe*.json"
                  ];
                  recipes =
                    let
                      steps = import ./src/client/env/steps.nix { inherit pkgs; };
                    in
                    {
                      probe_archive = [
                        steps.extract7z
                        steps.pickLargest
                        steps.keepExtension
                      ];
                      # A second handler in the same env: the dispatch case
                      # grows a branch and the tool exports merge across
                      # pipelines.
                      probe_scene = [
                        steps.unrar
                        steps.pickLargest
                        steps.keepExtension
                      ];
                      # The harkinian shape, unstubbed: unzip is small enough
                      # to run for real.
                      probe_zip = [
                        steps.unzip
                        steps.placeTree
                      ];
                      # A composition mistake: unzip leaves a directory
                      # cursor, which keep-extension must refuse rather than
                      # install <id>.unzip that nothing resolves as installed.
                      probe_dir_ext = [
                        steps.unzip
                        steps.keepExtension
                      ];
                      # Inline on purpose — this pins the harness, not a
                      # step. A tool must be bound, never exported: Info-ZIP's
                      # unzip reads an UNZIP environment variable as prepended
                      # arguments, so an exported binding hands unzip its own
                      # binary as the archive.
                      probe_hygiene = [
                        {
                          name = "assert-unexported";
                          tools.UNZIP = "${pkgs.unzip}/bin/unzip";
                          script = ''
                            [ -x "$UNZIP" ] || fail "UNZIP is not bound inside the script"
                            if env | grep -q '^UNZIP='; then
                              fail "UNZIP is exported — unzip would read it as prepended arguments"
                            fi
                            mkdir -p "$dest"
                          '';
                        }
                      ];
                    };
                };
          in
          assert pkgs.lib.assertMsg (
            harkinianProbe.recipes ? single_file && harkinianProbe.recipes ? no_intro_set
          ) "harkinianPort must carry its unzip recipe for both single_file and no_intro_set";
          pkgs.runCommand "check-recipes" { nativeBuildInputs = [ pkgs.zip ]; } ''
            export HOME=$TMPDIR
            mkdir -p $TMPDIR/bin

            cat > $TMPDIR/bin/unrar <<'EOF'
            #!${pkgs.runtimeShell}
            # unrar e -idq -o+ <rar> <stage/>: drop two "extracted" files, sizes apart.
            eval "stage=\''${$#}"
            name=game.xci
            [ "''${GOTG_TEST_UPPER:-0}" = 1 ] && name=GAME.XCI
            printf 'big' > "$stage/$name"
            printf 'x' > "$stage/readme.txt"
            EOF
            cat > $TMPDIR/bin/rhash <<'EOF'
            #!${pkgs.runtimeShell}
            [ "''${GOTG_TEST_SFV_FAILS:-0}" = 1 ] && exit 1
            exit 0
            EOF
            cat > $TMPDIR/bin/7z <<'EOF'
            #!${pkgs.runtimeShell}
            for arg in "$@"; do case "$arg" in -o*) stage="''${arg#-o}";; esac; done
            if [ "''${GOTG_TEST_7Z_EMPTY:-0}" = 1 ]; then exit 0; fi
            if [ "''${GOTG_TEST_7Z_MANY:-0}" = 1 ]; then
              for i in $(seq 3000); do
                printf 'x' > "$stage/padding-file-with-a-long-name-to-fill-the-pipe-buffer-$i.bin"
              done
            fi
            printf 'iso-bytes' > "$stage/game.iso"
            EOF
            cat > $TMPDIR/bin/dolphin-tool <<'EOF'
            #!${pkgs.runtimeShell}
            prev=""; in=""; out=""
            for arg in "$@"; do
              case "$prev" in -i) in="$arg";; -o) out="$arg";; esac
              prev="$arg"
            done
            [ -f "$in" ] || exit 1
            printf 'rvz-of:%s' "$(cat "$in")" > "$out"
            EOF
            chmod +x $TMPDIR/bin/*

            export GOTG_UNRAR=$TMPDIR/bin/unrar GOTG_RHASH=$TMPDIR/bin/rhash
            export GOTG_P7Z=$TMPDIR/bin/7z GOTG_DOLPHIN_TOOL=$TMPDIR/bin/dolphin-tool

            # Scene: sfv verified, largest file wins, staging swept.
            raw=$TMPDIR/raw-scene && mkdir -p $raw
            touch $raw/group.rar $raw/group.r00 $raw/group.sfv
            ${switch}/bin/gotg-recipe scene_archive $raw $TMPDIR/out/world.game
            [ "$(cat $TMPDIR/out/world.game.xci)" = big ]
            [ -z "$(ls -A $TMPDIR/out | grep gotg-recipe || true)" ]

            # Scene with a failing sfv: refused, nothing installed.
            if GOTG_TEST_SFV_FAILS=1 ${switch}/bin/gotg-recipe scene_archive $raw $TMPDIR/out2/x; then
              echo "a failing sfv must fail the recipe" >&2; exit 1
            fi
            [ ! -e $TMPDIR/out2/x.xci ]

            # Disc: extract then convert, chained through the stubs.
            raw=$TMPDIR/raw-disc && mkdir -p $raw
            touch "$raw/Game (USA).7z"
            ${gamecube}/bin/gotg-recipe single_archive $raw $TMPDIR/out/usa.game
            [ "$(cat $TMPDIR/out/usa.game.rvz)" = "rvz-of:iso-bytes" ]

            # An unknown handler is a refusal, not a guess.
            if ${switch}/bin/gotg-recipe mystery $raw $TMPDIR/out/y 2>$TMPDIR/err-dispatch; then
              echo "an unknown handler must fail" >&2; exit 1
            fi
            grep -q 'gotg-recipe\[dispatch\]' $TMPDIR/err-dispatch

            # A pipeline composed à la carte: extract, pick, keep the extension
            # — no conversion — through an env the tree does not ship.
            raw=$TMPDIR/raw-probe && mkdir -p $raw
            touch "$raw/Game (USA).7z"
            ${probe}/bin/gotg-recipe probe_archive $raw $TMPDIR/out/usa.probe
            [ "$(cat $TMPDIR/out/usa.probe.iso)" = iso-bytes ]

            # A step that fails names itself, and staging is swept even then.
            raw=$TMPDIR/raw-empty && mkdir -p $raw
            if ${probe}/bin/gotg-recipe probe_archive $raw $TMPDIR/out3/x 2>$TMPDIR/err; then
              echo "an empty raw dir must fail the extract step" >&2; exit 1
            fi
            grep -q 'gotg-recipe\[7z\]' $TMPDIR/err
            [ -z "$(ls -A $TMPDIR/out3 | grep gotg-recipe || true)" ]

            # Scene without an .sfv: absence is fine, only a failing check
            # refuses — and an uppercase extension is stored lowercase, or the
            # emulator cannot dispatch on it.
            raw=$TMPDIR/raw-nosfv && mkdir -p $raw
            touch $raw/group.rar $raw/group.r00
            GOTG_TEST_UPPER=1 ${switch}/bin/gotg-recipe scene_archive $raw $TMPDIR/out/nosfv.game
            [ "$(cat $TMPDIR/out/nosfv.game.xci)" = big ]

            # An extraction that succeeds but yields nothing fails
            # mid-pipeline: the picking step names itself, and the
            # already-populated staging is swept.
            raw=$TMPDIR/raw-hollow && mkdir -p $raw
            touch "$raw/Hollow.7z"
            if GOTG_TEST_7Z_EMPTY=1 ${probe}/bin/gotg-recipe probe_archive $raw $TMPDIR/out4/x 2>$TMPDIR/err2; then
              echo "an empty extraction must fail the pick step" >&2; exit 1
            fi
            grep -q 'gotg-recipe\[pick-largest\]' $TMPDIR/err2
            [ -z "$(ls -A $TMPDIR/out4 | grep gotg-recipe || true)" ]

            # An extraction with many files must still pick the largest: head
            # closing the pipe early must not kill the pipeline under pipefail.
            raw=$TMPDIR/raw-many && mkdir -p $raw
            touch "$raw/Many.7z"
            GOTG_TEST_7Z_MANY=1 ${probe}/bin/gotg-recipe probe_archive $raw $TMPDIR/out/many.probe
            [ "$(cat $TMPDIR/out/many.probe.iso)" = iso-bytes ]

            # A destination with spaces survives the quoting end to end —
            # staging is derived from its dirname.
            raw=$TMPDIR/raw-spaced && mkdir -p $raw
            touch "$raw/Game (USA).7z"
            ${probe}/bin/gotg-recipe probe_archive $raw "$TMPDIR/out dir/usa.probe"
            [ "$(cat "$TMPDIR/out dir/usa.probe.iso")" = iso-bytes ]

            # The second handler of a multi-handler env dispatches
            # independently.
            raw=$TMPDIR/raw-two && mkdir -p $raw
            touch $raw/group.rar
            ${probe}/bin/gotg-recipe probe_scene $raw $TMPDIR/out/two.game
            [ "$(cat $TMPDIR/out/two.game.xci)" = big ]

            # A zipped ROM becomes the unpacked tree at the bare destination —
            # the harkinian shape, with the real unzip.
            raw=$TMPDIR/raw-zip && mkdir -p $raw
            printf 'rom bytes' > "$TMPDIR/Zelda (USA).z64"
            (cd $TMPDIR && zip -q "$raw/usa.zelda.zip" "Zelda (USA).z64")
            ${probe}/bin/gotg-recipe probe_zip $raw $TMPDIR/out/usa.zelda
            [ "$(cat "$TMPDIR/out/usa.zelda/Zelda (USA).z64")" = "rom bytes" ]

            # A tree carrying a symlink is refused at the sink, whole.
            raw=$TMPDIR/raw-link && mkdir -p $raw/dir
            printf 'rom bytes' > "$raw/dir/game.z64"
            ln -s /etc/passwd "$raw/dir/escape"
            (cd $raw/dir && zip -qy "$raw/usa.linked.zip" game.z64 escape)
            rm -rf $raw/dir
            if ${probe}/bin/gotg-recipe probe_zip $raw $TMPDIR/out-link/x 2>$TMPDIR/err-link; then
              echo "a zip planting a symlink must be refused" >&2; exit 1
            fi
            grep -q 'symlink' $TMPDIR/err-link
            [ ! -e $TMPDIR/out-link/x ]

            # unzip leaves a directory cursor; keep-extension must refuse it.
            raw=$TMPDIR/raw-dirext && mkdir -p $raw
            cp $TMPDIR/raw-zip/usa.zelda.zip $raw/
            if ${probe}/bin/gotg-recipe probe_dir_ext $raw $TMPDIR/out5/x 2>$TMPDIR/err3; then
              echo "a directory cursor must fail keep-extension" >&2; exit 1
            fi
            grep -q 'gotg-recipe\[keep-extension\]' $TMPDIR/err3

            # probe_zip only fails on an exported tool by side effect; this
            # names the invariant.
            raw=$TMPDIR/raw-hygiene && mkdir -p $raw
            ${probe}/bin/gotg-recipe probe_hygiene $raw $TMPDIR/out/hygiene

            # An emulator told where to save must find that place existing —
            # ares reports a missing save path as read-only and the progress
            # of the session is lost. The wrapper creates the static prefix
            # of every declared saves glob, and only the static prefix.
            grep -qF 'mkdir -p "$state"/saves' ${probe}/bin/gotg-play
            grep -qF 'mkdir -p "$state"/data/probe' ${probe}/bin/gotg-play
            ! grep -F 'probe*' ${probe}/bin/gotg-play | grep -q mkdir

            # recipe.json is what download.sh consults; a migration must not
            # rename a handler on the wire.
            grep -q '"scene_archive"' ${switch}/share/gotg/recipe.json
            grep -q '"single_archive"' ${gamecube}/share/gotg/recipe.json

            touch $out
          '';

        # A DAT directory mapped to a platform with no environment imports
        # games nobody can launch. plan.py is pure stdlib, so this reads the
        # real map rather than a copy of it.
        platforms =
          pkgs.runCommand "check-platforms" { nativeBuildInputs = [ pkgs.python312 ]; } ''
            export PYTHONPATH=${./src}
            export GOTG_ENV_DIR=${./src/client/env}
            python3 ${./tests/indexer/check_platforms.py}
            touch $out
          '';

        ruff =
          pkgs.runCommand "check-ruff"
            {
              nativeBuildInputs = [ pkgs.ruff ];
            }
            ''
              cd ${./.}
              ruff check --no-cache src/gotg tests/conftest.py tests/service tests/indexer
              ruff format --no-cache --check src/gotg tests/conftest.py tests/service tests/indexer
              touch $out
            '';
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
