# Every launchable environment, discovered from the files next to this one.
#
#   <platform>.nix              the base environment for a platform
#   games/<platform>/<id>.nix   settings for one game, merged over that base
#
# The attribute names are the contract with the CLI: `gotg play` works out
# "env-<platform>" or "env-<platform>-<id>" from the catalog entry and the names
# of these files alone, so nothing is evaluated until an environment is actually
# missing and has to be built.
{
  pkgs,
  lib ? pkgs.lib,
  # danstick's launcher, for the one environment that has to sandbox the game
  # itself. Optional so this file still evaluates from a checkout with no
  # flake inputs -- the split-screen variants are what need it, and they say
  # so by failing to build rather than by launching a game with no isolation.
  danstick-rs ? null,
  # The SDL enumerator, for the one environment that has to ask what a game
  # will see rather than what the session can see. Optional for the same
  # reason as danstick-rs above.
  gotg-pads ? null,
  # Tools this project packages itself, for environments that need something
  # nixpkgs does not carry -- nix/tools.nix, which the flake's own packages are
  # built from too, so an environment names the derivation the flake exposes.
  # Passed alongside pkgs so an env file never has to reach back up the tree
  # with a relative path.
  tools ? (import ../../../nix/tools.nix { inherit pkgs; }).tools,
}:

let
  mkEnv = import ./machinery/lib.nix { inherit pkgs lib; };
  helpers = import ./machinery/helpers.nix { inherit pkgs lib; };

  gotgPkgs = tools // {
    inherit danstick-rs gotg-pads;
  };

  nixNames =
    dir:
    lib.mapAttrsToList (n: _: lib.removeSuffix ".nix" n) (
      lib.filterAttrs (n: t: t == "regular" && lib.hasSuffix ".nix" n) (builtins.readDir dir)
    );

  # Every file here but these two is a platform. The machinery (lib.nix,
  # helpers.nix, steps.nix) lives in machinery/, which this scan does not
  # enter, so a new helper cannot become a "platform" whose evaluation fails
  # -- as foreign-gl did, taking `nix flake check` and checks.environments
  # with it. foreign-gl.nix stays here only because the client, the picker,
  # the overlay and the QA tools import it by this path.
  platforms = lib.subtractLists [
    "default"
    "foreign-gl"
  ] (nixNames ./.);

  baseFor =
    platform:
    import (./. + "/${platform}.nix") {
      inherit
        pkgs
        lib
        helpers
        gotgPkgs
        ;
    };

  gamesFor =
    platform:
    let
      dir = ./games + "/${platform}";
    in
    if builtins.pathExists dir then nixNames dir else [ ];

  # An id holds exactly one dot, which would be read as an attribute path
  # separator in a flake reference, so it becomes an underscore. Since the dot is
  # always in the region prefix, no two ids can collapse onto the same name.
  #
  # That same rule makes variants unambiguous: a filename with a *second* dot is
  # "<id>.<variant>", because an id can never contain one. So
  # usa.super_mario_sunshine.bse.nix is the bse variant of that game, reachable
  # as `gotg play usa.super_mario_sunshine bse`.
  splitGame =
    name:
    let
      parts = lib.splitString "." name;
    in
    {
      id = lib.concatStringsSep "." (lib.take 2 parts);
      variant = if lib.length parts > 2 then lib.elemAt parts 2 else null;
    };

  attrFor =
    platform: id: variant:
    "env-${platform}-${lib.replaceStrings [ "." ] [ "_" ] id}"
    + lib.optionalString (variant != null) "-${variant}";

  # A game states only what it changes. Shallow, because `emulator` is a
  # derivation and a recursive merge would splice two of them together; the two
  # attributes a game is likely to want to add to rather than replace are merged
  # by hand.
  merge =
    base: patch:
    base
    // patch
    // lib.optionalAttrs (base ? env || patch ? env) {
      env = (base.env or { }) // (patch.env or { });
    }
    // lib.optionalAttrs (base ? configFiles || patch ? configFiles) {
      configFiles = (base.configFiles or { }) // (patch.configFiles or { });
    };

  # A platform's legacy locations are written for the whole platform — ares kept
  # every SNES save in ~/Games/snes, so the glob is "*.ram". Inherited unchanged
  # by a game with an environment of its own, that would have
  # env-snes-world_super_metroid adopt every SNES save on the machine and then
  # push them all as its own. Narrow the filename to the game, which is what the
  # save is named after; entries that are whole directories rather than globs —
  # the Harkinian ports' — have nothing to narrow and are left alone.
  scopeLegacyToGame =
    id: paths:
    map (entry: entry // { from = lib.replaceStrings [ "/*." ] [ "/${id}." ] entry.from; }) paths;

  gameEnv =
    platform: fileName:
    let
      inherit (splitGame fileName) id variant;

      # A variant builds on the game's own environment when it has one, and on
      # the platform otherwise — so a mod inherits whatever the plain game
      # already established rather than restating it.
      plainFile = ./games + "/${platform}/${id}.nix";
      platformBase = baseFor platform;
      base =
        if variant != null && builtins.pathExists plainFile then
          merge platformBase (
            import plainFile {
              inherit
                pkgs
                lib
                helpers
                gotgPkgs
                ;
              base = platformBase;
            }
          )
        else
          platformBase;

      patch = import (./games + "/${platform}/${fileName}.nix") {
        inherit
          pkgs
          lib
          base
          helpers
          gotgPkgs
          ;
      };
      merged = merge base patch;
    in
    mkEnv (
      merged
      // {
        name = attrFor platform id variant;
        legacyPaths = scopeLegacyToGame id (merged.legacyPaths or [ ]);
      }
    );
in
lib.listToAttrs (
  map (
    platform:
    lib.nameValuePair "env-${platform}" (mkEnv (baseFor platform // { name = "env-${platform}"; }))
  ) platforms
  ++ lib.concatMap (
    platform:
    map (
      fileName:
      let
        inherit (splitGame fileName) id variant;
      in
      lib.nameValuePair (attrFor platform id variant) (gameEnv platform fileName)
    ) (gamesFor platform)
  ) platforms
)
