# programs.gotg, for a home-manager machine: the library's picker and the
# games named here as packages, so a NixOS or home-manager person declares
# the games they play the way they declare everything else, and the picker
# still installs more by hand (docs/nix-games.md, phase 7).
#
#   programs.gotg = {
#     enable = true;
#     library = inputs.library;          # a flake made from gotg#library
#     games = [ "n64.usa.donkey_kong_64" "gamecube.usa.super_smash_bros_melee_rev2" ];
#   };
#
# Games are the same outputs `nix run <library>#...` runs, on PATH by the
# program each carries (gotg-n64-usa-donkey_kong_64). The token is not
# here: it is `nix run <library>#login`'s, written 0600 where only this user
# reads it, and a Nix option would copy it into the world-readable store.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.gotg;
  system = pkgs.stdenv.hostPlatform.system;
  gameOf =
    name:
    lib.attrByPath (lib.splitString "." name)
      (throw "programs.gotg.games: ${name} is not in the library")
      cfg.library.legacyPackages.${system};
in
{
  options.programs.gotg = {
    enable = lib.mkEnableOption "GOTG: the picker and the games of a library";
    library = lib.mkOption {
      type = lib.types.attrs;
      description = "The library flake (an input made from gotg#library): its server, its catalog pin, its games.";
    };
    games = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Games to build, as the library names them: platform.region.name, a variant after.";
    };
    picker = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "The picker, as gotg-ui on PATH.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages =
      lib.optional cfg.picker cfg.library.packages.${system}.gotg-ui ++ map gameOf cfg.games;
    # The apps know their library; a program on PATH does not, and the
    # picker's install builds from this one. A flake input has a store path;
    # a configured, writable library still wins (gotg_library).
    home.sessionVariables.GOTG_LIBRARY_DEFAULT = lib.mkIf (cfg.library ? outPath) (
      lib.mkDefault (toString cfg.library.outPath)
    );
  };
}
