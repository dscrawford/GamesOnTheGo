# One command, for a machine that has a token and nothing else of GOTG:
#
#   nix run github:dscrawford/GamesOnTheGo#play -- n64.usa.donkey_kong_64
#   GOTG_TOKEN=... nix run github:dscrawford/GamesOnTheGo#play -- usa.donkey_kong_64
#
# A library is a flake naming the server and pinning its catalog, and a game
# is its output; this makes that library where the config is, from the token
# in api.json (or GOTG_TOKEN, written there), pins it to the GOTG this ran
# from, and runs the game by the library's path -- which is where Nix will
# write a lock, unlike a registry name. Nothing is installed: no profile, no
# Steam entry, no udev rule, so controllers want install.sh's rule to be
# published, and the keyboard and mouse are what there is until then.
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

attr="${1:-}"
[[ -n "$attr" && "$attr" != -* ]] || die "usage: nix run <gotg>#play -- <platform>.<region>.<name>[.<variant>] [game args]
     e.g. n64.usa.donkey_kong_64"
shift

conf="${GOTG_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/gotg}"
api="$conf/api.json"
mkdir -p "$conf"
chmod 700 "$conf"

# The token: api.json, as `gotg login` leaves it, or GOTG_TOKEN for a first
# contact -- written there the same way, so a second run needs nothing.
if [[ ! -s "$api" ]]; then
  [[ -n "${GOTG_TOKEN:-}" ]] || die "no token here. Either:
     GOTG_TOKEN=<token> nix run <gotg>#play -- $attr        (GOTG_SERVER for a server other than ${GOTG_SERVER:-https://gotg.dcraw.net})
     nix run <gotg>#login -- --claim <invite url>; then this again"
  [[ "$GOTG_TOKEN" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || die "GOTG_TOKEN has characters no token uses"
  server="${GOTG_SERVER:-https://gotg.dcraw.net}"
  server="${server%/}"
  [[ "$server" == https://* || "$server" == http://127.0.0.1* || "$server" == http://localhost* ]] ||
    die "$server is http, which would send the token in the clear"
  (umask 077; jq -n --arg url "$server" --arg token "$GOTG_TOKEN" '{url: $url, token: $token}' >"$api")
fi
server="$(jq -r '.url // empty' "$api")"
[[ -n "$server" ]] || die "$api names no server"
server="${server%/}"
token="$(jq -r '.token // empty' "$api")"
[[ -n "$token" ]] || die "$api holds no token"

# Nix fetches the catalog with the token as a netrc password; the netrc is
# named outright, since nix.conf may not be this user's to write.
netrc="$conf/netrc"
host="${server#*://}"
host="${host%%/*}"
if ! [[ -s "$netrc" ]] || ! grep -q "^machine ${host%%:*} " "$netrc"; then
  (umask 077; printf 'machine %s login gotg password %s\n' "${host%%:*}" "$token" >>"$netrc")
fi
export NIX_CONFIG="netrc-file = $netrc${NIX_CONFIG:+
$NIX_CONFIG}"

# The library, pinned to the GOTG this ran from: what `nix run` resolved is
# what the game is built with, and no second fetch of the repository.
lib="${GOTG_LIBRARY:-$conf/library}"
if [[ ! -f "$lib/flake.nix" ]]; then
  mkdir -p "$lib"
  cat >"$lib/flake.nix" <<FLAKE
# A GOTG library, made by \`nix run <gotg>#play\`: the games of $server, each
# something \`nix run\` runs. nix search . zelda; nix run .#n64.usa.<id>.
{
  inputs = {
    gotg.url = "$GOTG_FLAKE";
    catalog = {
      url = "file+$server/catalog";
      flake = false;
    };
  };

  outputs =
    { self, gotg, catalog, ... }:
    gotg.lib.mkLibrary {
      server = "$server";
      inherit catalog;
      library = self;
    };
}
FLAKE
  printf 'made a library of %s in %s\n' "$server" "$lib" >&2
fi

exec nix run "$lib#$attr" -- "$@"
