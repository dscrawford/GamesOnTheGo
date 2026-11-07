# The `2p`, `3p` and `4p` files of a game that has a split-screen session.
#
# A variant file is the game's own environment with the session's launch
# composed over it; `players` is the whole configuration -- the layout, the
# window rules and the controller numbers follow from it inside the split
# function. Discovery wants one file per variant (the name is the variant), so
# each is a call.
#
# `base //` carries the game's Windows facet into the patch, where the
# inheritance rule (env/default.nix) would read it as the variant's own. A
# split-screen session is a nested compositor with a copy per player, Linux
# only, so the variant says so -- unless its split states a facet itself.
_: {
  coopVariant =
    {
      split,
      players,
      gotgPkgs,
      base,
    }:
    let
      patch = split { inherit gotgPkgs base players; };
    in
    base
    // patch
    // {
      windows =
        patch.windows or {
          reason = "the ${toString players}-player split screen runs a Linux-only nested session";
        };
    };
}
