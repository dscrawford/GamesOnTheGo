# gotg.lib.catalog against a fixture catalog and the real environment files:
# the tree the per-game outputs hang off, its short forms, and which
# environment each game runs in.
{ pkgs }:
let
  inherit (pkgs) lib;
  catalog = import ../../lib/catalog.nix { inherit lib; };
  data = catalog.read ../../tests/fixtures/catalog.json;
  envDir = ../../src/client/env;
  ids = f: games: f (g: "${g.platform}/${g.id}") games;
  env = platform: id: variant: catalog.envFor { inherit envDir platform id variant; };
  failures = lib.runTests {
    testTreeIsPlatformRegionName = {
      expr = (ids catalog.tree data.games).n64.usa.donkey_kong_64;
      expected = "n64/usa.donkey_kong_64";
    };
    testAnIdOnTwoPlatformsIsUnderBoth = {
      expr = with ids catalog.tree data.games; [ gb.usa.tetris_2 nes.usa.tetris_2 ];
      expected = [ "gb/usa.tetris_2" "nes/usa.tetris_2" ];
    };
    testTheShortFormIsForIdsOnOnePlatform = {
      expr = (ids catalog.unique data.games).usa.donkey_kong_64;
      expected = "n64/usa.donkey_kong_64";
    };
    testAnIdOnTwoPlatformsHasNoShortForm = {
      expr = (ids catalog.unique data.games).usa ? tetris_2;
      expected = false;
    };
    testAMalformedEntryIsLeftOut = {
      expr = (catalog.tree (g: g) [ { id = "../etc"; platform = "n64"; } { id = "usa.ok"; platform = "N64!"; } ]);
      expected = { };
    };
    testAGameWithAFileRunsInItsOwn = {
      expr = env "n64" "usa.donkey_kong_64" null;
      expected = { attr = "env-n64-usa_donkey_kong_64"; };
    };
    testAGameWithoutOneRunsInItsPlatforms = {
      expr = env "n64" "usa.zelda" null;
      expected = { attr = "env-n64"; };
    };
    testAVariantIsItsFile = {
      expr = env "n64" "usa.legend_of_zelda_ocarina_of_time_rev2" "rando";
      expected = { attr = "env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-rando"; };
    };
    testEmulateIsThePlatformsUnderAGameWithAFile = {
      expr = env "n64" "usa.donkey_kong_64" "emulate";
      expected = { attr = "env-n64"; };
    };
    testEmulateWhereItChangesNothingIsRefused = {
      expr = env "n64" "usa.zelda" "emulate" ? error;
      expected = true;
    };
    testAPlatformWithNoEnvironmentIsAnError = {
      expr = env "psx" "usa.crash_bandicoot" null ? error;
      expected = true;
    };
    testAVariantNameThatCouldBeAPathIsRefused = {
      expr = env "n64" "usa.donkey_kong_64" "../x" ? error;
      expected = true;
    };
    testVariantsAreTheFilesBesideAGames = {
      expr = lib.sort lib.lessThan (catalog.variantsOf { inherit envDir; platform = "n64"; id = "usa.super_mario_64"; });
      expected = [ "pc" "pc-2p" "pc-3p" "pc-4p" ];
    };
  };
in
if failures == [ ] then
  pkgs.runCommand "check-catalog" { } "touch $out"
else
  throw "gotg.lib.catalog: ${builtins.toJSON failures}"
