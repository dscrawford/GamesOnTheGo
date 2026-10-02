# programs.gotg evaluates against a library of the fixture catalog, with
# home-manager's two options stood in for: the picker and the named games
# become packages, a game the library lacks is a clear error, and nothing
# puts a token anywhere.
{ pkgs, flake }:
let
  inherit (pkgs) lib;
  # As a flake input arrives: the outputs, and where it is.
  library =
    flake.lib.mkLibrary {
      server = "https://gotg.example/";
      catalog = ../../tests/fixtures/catalog.json;
      library = ../../tests/fixtures;
    }
    // {
      outPath = ../../tests/fixtures;
    };
  stub = {
    options.home.packages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
    };
    options.home.sessionVariables = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
    };
  };
  evaluated =
    games:
    (lib.evalModules {
      modules = [
        stub
        ../modules/home-manager.nix
        {
          _module.args.pkgs = pkgs;
          programs.gotg = {
            enable = true;
            inherit library games;
          };
        }
      ];
    }).config;
  names = games: map (p: p.name) (evaluated games).home.packages;
  failures = lib.runTests {
    testThePickerAndTheGamesArePackages = {
      expr = names [
        "n64.usa.donkey_kong_64"
        "n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando"
      ];
      expected = [
        "gotg-ui-0.1.0"
        "gotg-game-n64.usa.donkey_kong_64"
        "gotg-game-n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando"
      ];
    };
    testTheLibraryIsTheDefault = {
      expr = lib.hasSuffix "tests/fixtures" (evaluated [ ]).home.sessionVariables.GOTG_LIBRARY_DEFAULT;
      expected = true;
    };
    testAGameTheLibraryLacksIsAnError = {
      expr = (builtins.tryEval (builtins.deepSeq (names [ "n64.usa.nowhere" ]) true)).success;
      expected = false;
    };
  };
in
if failures == [ ] then
  pkgs.runCommand "check-hm-module" { } "touch $out"
else
  throw "programs.gotg: ${builtins.toJSON failures}"
