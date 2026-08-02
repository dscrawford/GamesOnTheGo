# Game Boy — ares, which works out the console from the ROM header, with its saves
# redirected into this environment's own directory. See helpers.nix for why that
# redirect is needed and what the trailing slash is doing.
{ helpers, ... }:
helpers.aresPlatform { platform = "gb"; }
