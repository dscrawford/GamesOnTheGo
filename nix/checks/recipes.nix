# The recipe chains, with the heavy tools stubbed: what is asserted is the
# plumbing — sfv gate, staging, largest-file selection, conversion arguments,
# cleanup — not a real unrar or a real disc conversion.
{ pkgs, packages }:
let
  switch = packages.env-switch;
  gamecube = packages.env-gamecube;
  # The real harkinian envs are too heavy to build in a check (they
  # carry the ports); a dummy port pins the helper's contract at
  # eval time instead. Both handlers, because the same zip is
  # single_file from the games-root pass and no_intro_set from the
  # DAT torrent path.
  harkinianProbe =
    (import ../../src/client/env/helpers.nix {
      inherit pkgs;
      inherit (pkgs) lib;
    }).harkinianPort
      {
        port = pkgs.coreutils;
        bin = "true";
        appName = "probe";
        archives = [ ];
      };
  # The one game environment that composes the unzip recipe itself rather
  # than inheriting it from a helper. Imported with a stand-in for the port
  # so this stays an eval — building the real one would pull the whole
  # recompilation into a check about plumbing.
  dk64Probe = import ../../src/client/env/games/n64/usa.donkey_kong_64.nix {
    inherit pkgs;
    inherit (pkgs) lib;
    helpers = import ../../src/client/env/helpers.nix {
      inherit pkgs;
      inherit (pkgs) lib;
    };
    gotgPkgs = {
      dk64recomp = pkgs.coreutils;
    };
  };
  overrides = builtins.fromJSON (builtins.readFile ../../src/client/data/overrides.json);

  # An environment nothing ships, composing its own pipeline from the
  # step library — the check that steps are usable à la carte, not
  # only through the two canned recipes.
  probe =
    (import ../../src/client/env/lib.nix {
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
            steps = import ../../src/client/env/steps.nix { inherit pkgs; };
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
assert pkgs.lib.assertMsg (
  dk64Probe.recipes ? single_file && dk64Probe.recipes ? no_intro_set
) "the DK64 environment must carry its unzip recipe for both single_file and no_intro_set";
assert pkgs.lib.assertMsg (
  map (s: s.name) dk64Probe.recipes.single_file == [
    "unzip"
    "place-tree"
  ]
) "the DK64 environment must unpack through the step library, not by hand in preLaunch";
# The two halves of `unzip` live in different files and are silently useless
# apart: the recipe unpacks, and this flag is how the CLI knows the shape of
# the path before anything is built. A recipe without the flag installs a tree
# the CLI then looks for under the archive's name.
assert pkgs.lib.assertMsg (
  (overrides."n64/usa.donkey_kong_64" or { }).unzip or false
) "src/client/data/overrides.json must mark n64/usa.donkey_kong_64 unzip, to match its recipe";
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
''
