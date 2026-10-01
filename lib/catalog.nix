# The catalog, as Nix data: what `gotg refresh` keeps as manifest.json, read
# from the library flake's pinned `catalog` input instead.
#
# Pure functions of a catalog file and the environment directory. Nothing
# here builds; a game's environment is chosen here and built by whoever
# asks for it, which is what keeps one game's evaluation from touching the
# others. See docs/nix-games.md.
{ lib }:
let
  # The service's own shapes (gotg/contract.py), and the client's.
  idRe = "[a-z]{3,5}\\.[a-z0-9][a-z0-9_]*";
  platformRe = "[a-z0-9][a-z0-9_-]{0,15}";
  variantRe = "[a-z0-9][a-z0-9_-]*";
  matches = re: s: builtins.match re s != null;
in
rec {
  inherit idRe platformRe variantRe;

  # The catalog's games, version 2 only: a v1 manifest has no ids, no handler
  # and no member files, and would yield nulls a launch builds paths out of.
  read =
    file:
    let
      data = builtins.fromJSON (builtins.readFile file);
    in
    if data.version or null != 2 || !(builtins.isList (data.games or null)) then
      throw "${toString file} is not a version 2 GOTG catalog"
    else
      data;

  # Only entries whose id and platform are what everything downstream assumes
  # they are: an attribute name and a path component. Anything else in the
  # catalog is left out rather than becoming an attribute nobody could name.
  wellFormed = game: matches idRe game.id && matches platformRe game.platform;

  # "usa.donkey_kong_64" -> { region = "usa"; name = "donkey_kong_64"; }
  splitId =
    id:
    let
      parts = builtins.match "([a-z]{3,5})\\.(.*)" id;
    in
    {
      region = builtins.elemAt parts 0;
      name = builtins.elemAt parts 1;
    };

  # platform -> region -> name -> f game. Lazy in the values: naming one game
  # forces its group's names, not its neighbours' environments.
  tree =
    f: games:
    lib.mapAttrs (
      _: onPlatform:
      lib.mapAttrs (_: inRegion: lib.listToAttrs (map (g: lib.nameValuePair (splitId g.id).name (f g)) inRegion)) (
        lib.groupBy (g: (splitId g.id).region) onPlatform
      )
    ) (lib.groupBy (g: g.platform) (builtins.filter wellFormed games));

  # region -> name -> f game, for the ids on one platform only: the short
  # form, `gotg#usa.donkey_kong_64`. An id on two platforms has none, and the
  # platform has to be said.
  unique =
    f: games:
    let
      ok = builtins.filter wellFormed games;
      counts = lib.foldl' (acc: g: acc // { ${g.id} = (acc.${g.id} or 0) + 1; }) { } ok;
    in
    lib.mapAttrs (
      _: inRegion: lib.listToAttrs (map (g: lib.nameValuePair (splitId g.id).name (f g)) inRegion)
    ) (lib.groupBy (g: (splitId g.id).region) (builtins.filter (g: counts.${g.id} == 1) ok));

  # The environment a game runs in, as `gotg play` chooses it (env_attr in
  # lib/env.sh, which this must agree with -- checks.resolver holds them to
  # it): a variant's file, `emulate` for the platform's own emulator under a
  # game that has a file, the game's file, or its platform's. Either
  # { attr = "env-..."; } or { error = "..."; }.
  envFor =
    {
      envDir,
      platform,
      id,
      variant ? null,
    }:
    let
      envId = builtins.replaceStrings [ "." ] [ "_" ] id;
      gameFile = name: envDir + "/games/${platform}/${name}.nix";
      platformFile = envDir + "/${platform}.nix";
    in
    if variant != null then
      if !(matches variantRe variant) then
        { error = "invalid variant name: ${variant}"; }
      else if builtins.pathExists (gameFile "${id}.${variant}") then
        { attr = "env-${platform}-${envId}-${variant}"; }
      else if variant == "emulate" then
        if !(builtins.pathExists platformFile) then
          { error = "no emulator for platform '${platform}', so there is nothing to fall back to"; }
        else if !(builtins.pathExists (gameFile id)) then
          { error = "${id} already runs on ${platform}'s emulator; 'emulate' would change nothing"; }
        else
          { attr = "env-${platform}"; }
      else
        { error = "no '${variant}' variant of ${id}"; }
    else if builtins.pathExists (gameFile id) then
      { attr = "env-${platform}-${envId}"; }
    else if builtins.pathExists platformFile then
      { attr = "env-${platform}"; }
    else
      { error = "no environment for platform '${platform}'"; };

  # A game's variants: the files beside its own, `<id>.<variant>.nix`.
  variantsOf =
    {
      envDir,
      platform,
      id,
    }:
    let
      dir = envDir + "/games/${platform}";
      prefix = "${id}.";
      names = if builtins.pathExists dir then builtins.attrNames (builtins.readDir dir) else [ ];
    in
    builtins.filter (v: matches variantRe v) (
      map (n: lib.removeSuffix ".nix" (lib.removePrefix prefix n)) (
        builtins.filter (n: lib.hasPrefix prefix n && lib.hasSuffix ".nix" n && n != "${id}.nix") names
      )
    );
}
