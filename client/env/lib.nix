# Building one launchable environment: an emulator, the arguments it wants, and
# whatever settings a particular game needs, wrapped up as a single `gotg-play`.
#
# That wrapper is the entire contract with the CLI. `gotg play` builds the
# derivation for the game, then execs `$out/bin/gotg-play` with the ROM — so
# everything about how a game runs is decided here, in nix, and none of it can
# drift with whatever happens to be installed on the machine.
{ pkgs, lib }:

{
  # Attribute name of this environment, and the name of its state directory.
  name,
  # The package the emulator lives in, and which binary inside it to run.
  emulator,
  bin ? null,
  # The command line. Placeholders are substituted at launch: {target} is the
  # ROM, {install} the directory it was downloaded into, {state} this
  # environment's writable directory.
  args ? [ "{target}" ],
  # Environment variables, with the same placeholders.
  env ? { },
  # Extra commands on PATH, for preLaunch.
  path ? [ ],
  # Shell run before the emulator starts, with $target, $install and $state set.
  preLaunch ? "",
  # Files seeded into {state} on the first launch and left alone afterwards, so
  # that settings changed in the emulator's own UI survive. Values are paths,
  # derivations, or the text of the file.
  configFiles ? { },
  # Point XDG at {state}, so this environment's settings are its own.
  isolate ? false,
}:

let
  exe = if bin == null then lib.getExe emulator else "${emulator}/bin/${bin}";

  # Quote for the shell, then reopen the quoting around each placeholder: the
  # expansion has to sit outside the single quotes to happen at all, and inside
  # double quotes so a path with spaces stays one argument.
  render =
    s:
    lib.replaceStrings
      [ "{target}" "{install}" "{state}" ]
      [ "'\"$target\"'" "'\"$install\"'" "'\"$state\"'" ]
      (lib.escapeShellArg s);

  exports = lib.concatLines (lib.mapAttrsToList (k: v: "export ${k}=${render (toString v)}") env);

  sourceOf = v: if lib.isDerivation v || lib.isPath v then v else pkgs.writeText "gotg-config" v;

  seedConfig = lib.concatLines (
    lib.mapAttrsToList (rel: v: ''
      if [ ! -e "$state"/${lib.escapeShellArg rel} ]; then
        mkdir -p "$(dirname "$state"/${lib.escapeShellArg rel})"
        cp --no-preserve=mode ${sourceOf v} "$state"/${lib.escapeShellArg rel}
      fi
    '') configFiles
  );
in
pkgs.writeShellApplication {
  name = "gotg-play";
  runtimeInputs = [ pkgs.coreutils ] ++ path;
  text = ''
    # usage: gotg-play [rom] [extra emulator arguments]
    #
    # `gotg play` passes the ROM it resolved from the catalog; running this
    # straight out of the store takes the same argument, which is most of the
    # point of building it this way.
    target="''${1:-''${GOTG_TARGET:-}}"
    if [ "$#" -gt 0 ]; then shift; fi
    if [ -z "$target" ]; then
      echo "usage: gotg-play <rom>" >&2
      exit 1
    fi

    # Part of the contract whether or not this environment refers to it.
    # shellcheck disable=SC2034
    install="''${GOTG_INSTALL:-$target}"

    state="''${GOTG_ENV_STATE:-''${XDG_STATE_HOME:-$HOME/.local/state}/gotg/env/${name}}"
    mkdir -p "$state"
    ${lib.optionalString isolate ''
      export XDG_CONFIG_HOME="$state/config"
      export XDG_DATA_HOME="$state/data"
      mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
    ''}
    ${seedConfig}
    ${exports}
    ${preLaunch}
    exec ${exe} ${lib.concatMapStringsSep " " render args} "$@"
  '';
}
