# The fullscreen placeholder has to expand to *no argument*, not to an empty
# one — ares reads an empty argument as a ROM path. The first attempt rendered
# ''$gotg_fullscreen'', which still forms a word, and nothing downstream would
# have complained. Grepped from a real built launcher rather than asserted
# about the nix, because the quoting is exactly what nix's escaping is doing
# to it.
{ pkgs }:
let
  env = (import ../../src/client/env { inherit pkgs; }).env-gb;
in
pkgs.runCommand "check-fullscreen" { } ''
  launcher="${env}/bin/gotg-play"
  grep -q 'gotg_fullscreen=""' "$launcher" \
    || { echo "no fullscreen decision in the launcher" >&2; exit 1; }
  grep -q '\[ -t 1 \] || gotg_fullscreen=' "$launcher" \
    || { echo "the terminal test is gone" >&2; exit 1; }
  # Space either side, so it is bare. The bug put a pair of single
  # quotes where these spaces are, which still forms a word when the
  # variable is empty — so this one pattern is the whole guard.
  grep -qF ' ''$gotg_fullscreen ' "$launcher" \
    || { echo "fullscreen is not expanded bare; an empty one would be an argument" >&2
         grep -n '^exec' "$launcher" >&2
         exit 1; }
  touch $out
''
