# mkLibrary: the outputs of a library flake -- a pin of this repository and a
# server's catalog in, one `nix run`-able output per game out. Its own file
# because flake.nix is the wiring of this repository's outputs and this is a
# function other flakes call (templates/library, through `gotg.lib.mkLibrary`);
# it was a hundred lines in the middle of the wiring. See docs/nix-games.md.
{
  lib,
  # system -> the one nixpkgs (allowUnfree) the flake uses for that system.
  pkgsFor,
  # system -> this flake's packages: the client, the picker, the environments.
  packagesFor,
  # lib/library.nix, applied to the catalog reader: what a game resolves to.
  libraryLib,
  # src/client/env, the files the resolver reads the names of.
  envDir,
}:
let
  inherit (import ./app.nix) shellApp;
in
{
  # Where the library is: the server a token is held for, and the one a
  # launch from this library talks to.
  server,
  # The catalog file, as the library flake's `catalog` input pins it.
  catalog,
  # The library flake itself (`self`), so its apps know where they came from:
  # `nix run <library>#ui` is a picker that builds games from this library
  # with nothing configured.
  library ? null,
  systems ? [ "x86_64-linux" ],
}:
{
  # The client, the picker and the QA tools, as this library's gotg builds
  # them: what `gotg update` puts where Steam starts them, and what `gotg qa`
  # grades with. And the catalog the lock pins, buildable so a client can
  # keep a root to it: that is its cache (library_catalog_refresh).
  packages = lib.genAttrs systems (system: {
    inherit (packagesFor system) gotg gotg-ui qa-tools;
    catalog = (pkgsFor system).runCommand "gotg-catalog" { } "ln -s ${catalog} $out";
  });

  # What a person runs, from the library: the picker, the Steam entries, the
  # rebuild after an upgrade, and the login -- each knowing this library and
  # its server, so none of them needs a `gotg` on PATH or anything configured
  # first.
  apps = lib.genAttrs systems (
    system:
    let
      pkgs = pkgsFor system;
      inherit (packagesFor system) gotg gotg-ui;
      app =
        name: text:
        shellApp pkgs {
          inherit name;
          text = ''
            ${lib.optionalString (
              library != null
            ) ''export GOTG_LIBRARY_DEFAULT="''${GOTG_LIBRARY_DEFAULT:-${library}}"''}
            ${text}
          '';
        };
    in
    {
      ui = app "gotg-library-ui" ''exec ${gotg-ui}/bin/gotg-ui "$@"'';
      steam = app "gotg-library-steam" ''exec ${gotg}/bin/gotg steam "$@"'';
      update = app "gotg-library-update" ''exec ${gotg}/bin/gotg update "$@"'';
      login = app "gotg-library-login" ''exec ${gotg}/bin/gotg login --server ${lib.escapeShellArg server} "$@"'';
      default = app "gotg-library-ui" ''exec ${gotg-ui}/bin/gotg-ui "$@"'';
    }
  );

  legacyPackages = lib.genAttrs systems (
    system:
    libraryLib.forSystem {
      pkgs = pkgsFor system;
      envs = lib.filterAttrs (name: _: lib.hasPrefix "env-" name) (packagesFor system);
      inherit (packagesFor system) gotg;
      inherit envDir server catalog;
    }
  );
}
