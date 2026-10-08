# The `2p`, `3p` and `4p` files of a game that has a split-screen session.
#
# Nine files (Super Mario 64, Ocarina of Time and Four Swords Adventures, two,
# three and four players each) were the same eight lines apart from the number
# and the name of the split function, each opening with a header that said the
# same thing nine ways. What a variant file is is stated once here: the game's
# own environment, with the session's launch composed over it. `players` is
# the whole configuration -- the layout, the window rules and the controller
# numbers follow from it inside the split function.
#
# The discovery contract is still one file per variant (the name is the
# variant), so the nine files remain, a call each.
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
