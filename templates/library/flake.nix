# A GOTG library: the games one server has, each something `nix run` runs.
#
#   nix run .#n64.usa.donkey_kong_64
#   nix run .#ui                   # the picker; .#steam, .#update, .#login likewise
#   nix registry add gotg "$PWD"   # then, from anywhere: nix run gotg#usa.donkey_kong_64
#
# The catalog is the server's, pinned here by flake.lock; the website updates
# it, and `nix flake update catalog` brings that here. It is private: Nix
# fetches it with the token `gotg login` keeps in ~/.config/gotg/netrc.
# See docs/nix-games.md in the GOTG repository.
{
  inputs = {
    # The feat/nix-games branch until it is merged: mkLibrary is there.
    gotg.url = "git+ssh://git@github.com/dscrawford/GamesOnTheGo?ref=feat/nix-games";
    catalog = {
      url = "file+https://gotg.dcraw.net/catalog";
      flake = false;
    };
  };

  outputs =
    { self, gotg, catalog, ... }:
    gotg.lib.mkLibrary {
      # The server this library is: where the catalog came from, and the
      # one a launch logs into.
      server = "https://gotg.dcraw.net";
      inherit catalog;
      library = self;
    };
}
