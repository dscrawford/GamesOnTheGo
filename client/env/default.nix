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

  nixNames =
    dir:
    lib.mapAttrsToList (n: _: lib.removeSuffix ".nix" n) (
      lib.filterAttrs (n: t: t == "regular" && lib.hasSuffix ".nix" n) (builtins.readDir dir)
    );

  platforms = lib.subtractLists [ "default" "lib" ] (nixNames ./.);

  baseFor = platform: import (./. + "/${platform}.nix") { inherit pkgs lib; };

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

  gameEnv =
    platform: id:
    let
      base = baseFor platform;
      patch = import (./games + "/${platform}/${id}.nix") { inherit pkgs lib base; };
    in
    mkEnv (merge base patch // { name = attrFor platform id; });
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
