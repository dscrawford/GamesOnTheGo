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
}:

let
  mkEnv = import ./lib.nix { inherit pkgs lib; };
  helpers = import ./helpers.nix { inherit pkgs lib; };

  nixNames =
    dir:
    lib.mapAttrsToList (n: _: lib.removeSuffix ".nix" n) (
      lib.filterAttrs (n: t: t == "regular" && lib.hasSuffix ".nix" n) (builtins.readDir dir)
    );

  platforms = lib.subtractLists [
    "default"
    "lib"
    "helpers"
  ] (nixNames ./.);

  baseFor = platform: import (./. + "/${platform}.nix") { inherit pkgs lib helpers; };

  gamesFor =
    platform:
    let
      dir = ./games + "/${platform}";
    in
    if builtins.pathExists dir then nixNames dir else [ ];

  # An id holds exactly one dot, which would be read as an attribute path
  # separator in a flake reference, so it becomes an underscore. Since the dot is
  # always in the region prefix, no two ids can collapse onto the same name.
  attrFor = platform: id: "env-${platform}-${lib.replaceStrings [ "." ] [ "_" ] id}";

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
    map (
      entry: entry // { from = lib.replaceStrings [ "/*." ] [ "/${id}." ] entry.from; }
    ) paths;

  gameEnv =
    platform: id:
    let
      base = baseFor platform;
      patch = import (./games + "/${platform}/${id}.nix") { inherit pkgs lib base helpers; };
      merged = merge base patch;
    in
    mkEnv (
      merged
      // {
        name = attrFor platform id;
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
    map (id: lib.nameValuePair (attrFor platform id) (gameEnv platform id)) (gamesFor platform)
  ) platforms
)
