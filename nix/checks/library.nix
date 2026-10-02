# gotg.lib.mkLibrary over the fixture catalog: what each game would launch,
# read from its spec without building it, and that asking for one game forces
# no other -- the fixture's PlayStation game has no environment, and only
# forcing it may fail.
{ pkgs, flake }:
let
  inherit (pkgs) lib;
  games =
    (flake.lib.mkLibrary {
      server = "https://gotg.example/";
      catalog = ../../tests/fixtures/catalog.json;
    }).legacyPackages.${pkgs.stdenv.hostPlatform.system};
  spec = drv: drv.gotgSpec // { env = builtins.unsafeDiscardStringContext drv.gotgSpec.env; };
  forced = v: (builtins.tryEval (builtins.seq v.drvPath true)).success;
  failures = lib.runTests {
    testAGameLaunchesItsOwnEnvironment = {
      expr = (spec games.n64.usa.donkey_kong_64).attr;
      expected = "env-n64-usa_donkey_kong_64";
    };
    testItsEnvironmentIsTheFlakesBuild = {
      expr = (spec games.n64.usa.donkey_kong_64).env;
      expected = builtins.unsafeDiscardStringContext "${flake.packages.${pkgs.stdenv.hostPlatform.system}.env-n64-usa_donkey_kong_64}";
    };
    testTheServerIsTheLibrarysWithoutASlash = {
      expr = (spec games.n64.usa.donkey_kong_64).server;
      expected = "https://gotg.example";
    };
    testTheBytesAreWhereTheCatalogSays = {
      expr = (spec games.n64.usa.donkey_kong_64).files_urls;
      expected = [ "https://files.example" ];
    };
    testTheGameIsTheCatalogsEntry = {
      expr = (spec games.n64.usa.donkey_kong_64).game.files;
      expected = (builtins.head (builtins.fromJSON (builtins.readFile ../../tests/fixtures/catalog.json)).games).files;
    };
    testAGameWithoutAFileRunsInItsPlatforms = {
      expr = (spec games.n64.usa.zelda).attr;
      expected = "env-n64";
    };
    testAVariantIsAnAttributeOfItsGame = {
      expr = with spec games.n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando; [ attr variant ];
      expected = [ "env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-rando" "rando" ];
    };
    testEmulateIsOfferedUnderAGameWithAFile = {
      expr = (spec games.n64.usa.donkey_kong_64.emulate).attr;
      expected = "env-n64";
    };
    testEmulateIsNotOfferedWhereItChangesNothing = {
      expr = games.n64.usa.zelda ? emulate;
      expected = false;
    };
    testTheShortFormIsTheSameGame = {
      expr = (spec games.usa.donkey_kong_64).attr;
      expected = "env-n64-usa_donkey_kong_64";
    };
    testAnIdOnTwoPlatformsNeedsItsPlatform = {
      expr = [ (games.usa ? tetris_2) (spec games.gb.usa.tetris_2).attr (spec games.nes.usa.tetris_2).attr ];
      expected = [ false "env-gb" "env-nes" ];
    };
    testAGameWithNoEnvironmentFailsOnlyWhenRun = {
      expr = [ (forced games.psx.usa.crash_bandicoot) (forced games.n64.usa.donkey_kong_64) ];
      expected = [ false true ];
    };
    testTheSpecFileIsTheOneTheGameCarries = {
      expr = lib.hasInfix (builtins.unsafeDiscardStringContext "${games.n64.usa.donkey_kong_64.gotgSpecFile}") (
        builtins.unsafeDiscardStringContext games.n64.usa.donkey_kong_64.drvAttrs.buildCommand or ""
      );
      expected = true;
    };
    testNixRunFindsTheProgram = {
      expr = games.n64.usa.donkey_kong_64.meta.mainProgram;
      expected = "gotg-n64-usa-donkey_kong_64";
    };
  };
in
if failures == [ ] then
  pkgs.runCommand "check-library" { } "touch $out"
else
  throw "gotg.lib.mkLibrary: ${builtins.toJSON failures}"
