# The Nix resolver (lib/catalog.nix envFor) and the client's (env_attr in
# lib/env.sh) must choose the same environment for every game, or a game
# would run one way from `nix run` and another from `gotg play`. Every game
# in the fixture and one per game file under env/games, each with no variant,
# each variant it has, `emulate`, and one it does not have.
{ pkgs, packages }:
let
  inherit (pkgs) lib;
  catalog = import ../../lib/catalog.nix { inherit lib; };
  envDir = ../../src/client/env;
  fixture = (catalog.read ../../tests/fixtures/catalog.json).games;
  platforms = builtins.attrNames (lib.filterAttrs (_: t: t == "directory") (builtins.readDir (envDir + "/games")));
  # One synthetic entry per game a file names: its id is the file name up to
  # the second dot.
  fromFiles = lib.concatMap (
    platform:
    map (file: {
      inherit platform;
      id = builtins.head (builtins.match "([a-z]{3,5}\\.[a-z0-9_]+).*" file);
      handler = "single_file";
      title = file;
      files = [ { name = "x.bin"; size_bytes = 1; sha256 = lib.fixedWidthString 64 "0" ""; } ];
    }) (builtins.attrNames (builtins.readDir (envDir + "/games/${platform}")))
  ) platforms;
  # One per platform and id, the fixture's first: two entries under one id
  # on one platform is a catalog the client rightly calls ambiguous.
  games = builtins.attrValues (
    lib.foldl' (acc: g: { "${g.platform}/${g.id}" = g; } // acc) { } (fixture ++ fromFiles)
  );
  cases = lib.concatMap (
    g:
    map (variant: {
      inherit (g) platform id;
      # "-" for none: tab is whitespace to `read`, and an empty field between
      # two would vanish and shift the rest.
      variant = if variant == null then "-" else variant;
      want =
        let
          r = catalog.envFor { inherit envDir variant; inherit (g) platform id; };
        in
        r.attr or "error";
    }) ([ null "emulate" "nope" ] ++ catalog.variantsOf { inherit envDir; inherit (g) platform id; })
  ) games;
  manifest = pkgs.writeText "manifest.json" (builtins.toJSON { version = 2; inherit games; });
  table = pkgs.writeText "cases.tsv" (lib.concatMapStrings (c: "${c.platform}\t${c.id}\t${c.variant}\t${c.want}\n") cases);
in
pkgs.runCommand "check-resolver" { nativeBuildInputs = [ pkgs.jq pkgs.bash ]; } ''
  export HOME=$TMPDIR GOTG_STATE_DIR=$TMPDIR/state GOTG_CONFIG_DIR=$TMPDIR/config
  # Where common.sh looks for it, whatever GOTG_CACHE_FILE says.
  mkdir -p $GOTG_STATE_DIR && cp ${manifest} $GOTG_STATE_DIR/manifest.json
  export GOTG_ENV_DIR=${envDir} NO_COLOR=1
  root=${packages.gotg}/share/gotg
  export GOTG_ROOT=$root GOTG_LIB=$root/lib GOTG_DATA=$root/data GOTG_TEMPLATES=$root/templates
  bash -c '
    set -uo pipefail
    for lib in color common config manifest env; do . "$GOTG_LIB/$lib.sh"; done
    bad=0
    while IFS=$'"'"'\t'"'"' read -r platform id variant want; do
      [ "$variant" != - ] || variant=""
      got=$( (env_attr "$(manifest_find "$platform/$id")" "$variant") 2>$TMPDIR/err ) || got=error
      [ "$got" != error ] || [ -n "''${shown:-}" ] || { shown=1; echo "first refusal said: $(cat $TMPDIR/err)" >&2; }
      if [ "$got" != "$want" ]; then
        echo "$platform/$id ${"$"}{variant:-(none)}: nix says $want, gotg play says $got" >&2
        bad=1
      fi
    done < ${table}
    exit $bad
  '
  echo "$(wc -l < ${table}) cases agree"
  touch $out
''
