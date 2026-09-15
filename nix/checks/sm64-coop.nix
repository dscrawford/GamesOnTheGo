# The co-op session a Super Mario 64 launch writes is one SplitScreenWrapper
# will accept — run, not grepped, and validated by the wrapper's own loader.
#
# The first version of that generator produced nothing at all: `jq --args`
# reads a positional beginning with a dash as an option. Every launcher still
# built. Only running it says so.
{ pkgs }:
let
  envs = import ../../src/client/env { inherit pkgs; };
  lib = pkgs.lib;

  # The generator, lifted out of the built launcher: from the first line it
  # writes to the jq that saves the session. The compile step above it wants a
  # ROM and several minutes, and has nothing to do with the shape of the file.
  check =
    players:
    let
      name = "env-n64-usa_super_mario_64-pc-${toString players}p";
      env = envs.${name};
    in
    ''
      echo "== ${name}"
      sed -n '/^gotg_coop=/,/splitscreen\/session.json"$/p' \
        ${env}/bin/gotg-play >"$TMPDIR/gen-${toString players}.sh"

      # A generator that stopped starting with that line would extract to
      # nothing, and nothing is a file that passes every assertion below.
      grep -q 'session.json' "$TMPDIR/gen-${toString players}.sh" ||
        { echo "${name}: found no session generator in the launcher" >&2; exit 1; }

      state="$TMPDIR/state-${toString players}"
      mkdir -p "$state"
      ( set -euo pipefail
        export state GOTG_USER_CONFIG="$TMPDIR/no-config"
        # shellcheck source=/dev/null
        source "$TMPDIR/gen-${toString players}.sh" )

      python3 ${./sm64-coop-session.py} "$state/splitscreen/session.json" ${toString players}

      # Twice, because it runs on every launch: the controller lines are
      # rewritten rather than appended, or a config gathers a new pair every
      # time somebody plays.
      ( set -euo pipefail
        export state GOTG_USER_CONFIG="$TMPDIR/no-config"
        # shellcheck source=/dev/null
        source "$TMPDIR/gen-${toString players}.sh" )
      for player in $(seq 1 ${toString players}); do
        lines="$(grep -c '^gamepad_number ' "$state/coop/p$player/sm64config.txt")"
        [ "$lines" = 1 ] ||
          { echo "${name}: p$player has $lines gamepad_number lines, not 1" >&2; exit 1; }
      done
    '';
in
pkgs.runCommand "check-sm64-coop"
  {
    nativeBuildInputs = [
      pkgs.jq
      pkgs.gnused
      pkgs.gnugrep
      (pkgs.python3.withPackages (ps: [ ps.i3ipc ]))
    ];
    # The wrapper's config module is the schema this has to satisfy; borrowing
    # it beats writing down a copy that can drift.
    PYTHONPATH = "${(pkgs.callPackage ../../pkgs/splitscreen { })}/share/splitscreen";
  }
  ''
    ${lib.concatMapStringsSep "\n" check [
      2
      3
      4
    ]}
    touch $out
  ''
