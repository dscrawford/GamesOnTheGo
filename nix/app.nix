# The shape of a flake app, and the one-script app built on it: the flake's
# `apps` and the library's (nix/library.nix) both write
# `{ type = "app"; program = ...; }` around a writeShellApplication.
let
  mkApp = program: {
    type = "app";
    inherit program;
  };
in
{
  inherit mkApp;

  # An app that is one shell script: its program is the script's bin/<name>.
  # `args` are writeShellApplication's.
  shellApp = pkgs: args: mkApp "${pkgs.writeShellApplication args}/bin/${args.name}";
}
