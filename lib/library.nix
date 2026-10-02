# A library: every game in a catalog as something `nix run` runs.
#
#   nix run gotg#n64.usa.donkey_kong_64             platform.region.game
#   nix run gotg#usa.donkey_kong_64                 an id on one platform only
#   nix run gotg#n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando
#
# A game is a small app that runs `gotg launch --spec` with its spec baked in:
# the catalog entry, the server, where its bytes are, and its environment --
# which is a dependency of the app, so Nix builds it and nothing at launch
# does. Everything else a launch does is the client's, unchanged (cmd-launch.sh).
#
# Lazy: the catalog is read whole to know the names, and a game's app -- and
# its environment -- is evaluated only when that game is asked for. A game
# with no environment is an attribute that says so when it is run, not an
# error for everyone else's evaluation. See docs/nix-games.md.
{
  lib,
  catalogLib,
}:
{
  # pkgs: the system's nixpkgs; envs: the flake's env-* packages; gotg: the
  # client that launches; envDir: the environment files the resolver reads;
  # server: where the library is (the token this machine holds is for it);
  # catalog: the catalog file, as pinned by the library flake's input.
  forSystem =
    {
      pkgs,
      envs,
      gotg,
      envDir,
      server,
      catalog,
    }:
    let
      data = catalogLib.read catalog;
      filesUrls = lib.unique ((data.files_urls or [ ]) ++ lib.optional (data ? files_url) data.files_url);

      app =
        game: variant:
        let
          chosen = catalogLib.envFor {
            inherit envDir variant;
            inherit (game) platform id;
          };
          suffix = lib.optionalString (variant != null) ".${variant}";
          name = "${game.platform}.${game.id}${suffix}";
          env =
            envs.${chosen.attr} or (throw "${name}: ${chosen.attr} is not an environment this flake builds");
          fields = {
            version = 1;
            server = lib.removeSuffix "/" server;
            files_urls = filesUrls;
            inherit (chosen) attr;
            variant = if variant == null then "" else variant;
            env = "${env}";
            game = builtins.removeAttrs game [ "path" ];
          };
          spec = pkgs.writeText "gotg-spec-${name}.json" (builtins.toJSON fields);
        in
        if chosen ? error then
          throw "${name}: ${chosen.error}"
        else
          # The spec rides along as an attribute too: what a game would launch,
          # readable without building it (checks.library, `nix eval`).
          (
            let
              program = "gotg-${builtins.replaceStrings [ "." ] [ "-" ] name}";
              launcher = pkgs.writeShellApplication {
                name = program;
                text = ''
                  exec ${gotg}/bin/gotg launch --spec ${spec} "$@"
                '';
              };
            in
            # The program, and the spec beside it at share/gotg/spec.json: a
            # client finds an environment's store path from the games built
            # here by reading these, with no evaluation (env_root).
            pkgs.runCommand "gotg-game-${name}"
              {
                meta = {
                  mainProgram = program;
                  description = "${game.title or game.id} (${game.platform})${
                    lib.optionalString (variant != null) ", ${variant}"
                  }";
                };
              }
              ''
                mkdir -p $out/bin $out/share/gotg
                ln -s ${launcher}/bin/${program} $out/bin/${program}
                ln -s ${spec} $out/share/gotg/spec.json
              ''
          )
          // {
            gotgSpec = fields;
            # The file itself, for `gotg qa --spec`: the very spec this game
            # launches with.
            gotgSpecFile = spec;
          };

      # A game, with its variants -- and `emulate`, for one that has a file of
      # its own -- as attributes of it. Still a derivation: `nix run` on the
      # game runs the game.
      withVariants =
        game:
        let
          variants =
            catalogLib.variantsOf {
              inherit envDir;
              inherit (game) platform id;
            }
            ++ lib.optional (builtins.pathExists (envDir + "/games/${game.platform}/${game.id}.nix")) "emulate";
        in
        app game null // lib.genAttrs variants (app game);

      byPlatform = catalogLib.tree withVariants data.games;
      # The short forms, without shadowing a platform: a region named like one
      # would turn `gotg#nes...` into something else.
      short = removeAttrs (catalogLib.unique withVariants data.games) (builtins.attrNames byPlatform);
      # Marked for `nix search <library> zelda`, which walks only the sets
      # that ask: that is the list a library has, now that the client has
      # none. A game's variants hang off the game and are not walked.
      searchable = lib.mapAttrs (_: lib.recurseIntoAttrs);
    in
    searchable (lib.mapAttrs (_: searchable) byPlatform) // searchable short;
}
