# The `2p`, `3p` and `4p` files of a game that has a split-screen session.
#
# A variant file is the game's own environment with the session's launch
# composed over it; `players` is the whole configuration -- the layout, the
# window rules and the controller numbers follow from it inside the split
# function. Discovery wants one file per variant (the name is the variant), so
# each is a call.
_: {
  coopVariant =
    {
      split,
      players,
      gotgPkgs,
      base,
    }:
    base // split { inherit gotgPkgs base players; };
}
