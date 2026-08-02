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
  # What is worth carrying between machines: globs under {state}, and what to
  # leave out of them. These live here rather than in data/overrides.json
  # because the glob and the emulator flag that *creates* the path it matches
  # are one decision — split them across two files and they drift silently, so
  # that changing where saves are written leaves a backup quietly holding
  # nothing. overrides.json is also keyed by game, which is the wrong shape for
  # an env-snes shared by every SNES title.
  saves ? [ ],
  saveExcludes ? [ ],
  # Where this emulator kept its saves before it was told to write them under
  # {state}. Read by `gotg saves adopt`, which copies them forward.
  legacyPaths ? [ ],
  # Save states are excluded by default: they run to tens of megabytes and are
  # not portable across emulator versions, so a state pulled from another
  # machine may simply refuse to load.
  saveStates ? false,
  # The ares console section whose controller bindings can be generated —
  # "SuperFamicom", "Nintendo64". Null leaves an environment's bindings alone.
  # ares creates a section the first time that console is run, so the name is
  # not derivable from the platform slug and is only set where it is confirmed.
  padConsole ? null,
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

  # Hints every emulator here needs, merged *under* an environment's own env so
  # that a platform can still override one.
  baseEnv = {
    # The current Steam Controller has no evdev node at all — it is a hidapi
    # device, driven by SDL3's triton driver. That driver's IsEnabled() falls
    # back to SDL_HINT_JOYSTICK_HIDAPI when no Steam-specific hint is set, and
    # the Steam launcher template deliberately sets that to 0, so the puck would
    # stay a keyboard and mouse inside exactly the launcher people use.
    #
    # An explicit hint beats the fallback, which is why this is set here rather
    # than by deleting the launcher's line: that line is what makes Steam Input
    # work for whoever is playing today, and it is not ours to regress.
    SDL_JOYSTICK_HIDAPI_STEAM = "1";
  };

  exports = lib.concatLines (
    lib.mapAttrsToList (k: v: "export ${k}=${render (toString v)}") (baseEnv // env)
  );

  sourceOf = v: if lib.isDerivation v || lib.isPath v then v else pkgs.writeText "gotg-config" v;

  seedConfig = lib.concatLines (
    lib.mapAttrsToList (rel: v: ''
      if [ ! -e "$state"/${lib.escapeShellArg rel} ]; then
        mkdir -p "$(dirname "$state"/${lib.escapeShellArg rel})"
        cp --no-preserve=mode ${sourceOf v} "$state"/${lib.escapeShellArg rel}
      fi
    '') configFiles
  );
  # Read by the CLI from the built GC root: a file read, never a nix evaluation,
  # the same rule env_attr already obeys so that nothing on the launch or the
  # sync path needs nix to be usable.
  manifest = {
    version = 1;
    inherit
      name
      saves
      saveStates
      ;
    excludes = saveExcludes;
    legacy = legacyPaths;
  };

  app = pkgs.writeShellApplication {
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
  };
in
# writeShellApplication has no postInstall to hang the manifest off, and
# overrideAttrs cannot reach inside it, so the runnable is wrapped rather than
# modified. env_is_built tests [[ -x ]], which follows the symlink, so nothing
# downstream can tell the difference.
pkgs.runCommand "gotg-env-${name}"
  {
    meta.mainProgram = "gotg-play";
  }
  ''
    mkdir -p $out/bin $out/share/gotg
    ln -s ${app}/bin/gotg-play $out/bin/gotg-play
    cp ${pkgs.writeText "saves.json" (builtins.toJSON manifest)} $out/share/gotg/saves.json
    ${lib.optionalString (padConsole != null) ''
      cp ${
        pkgs.writeText "pads.json" (builtins.toJSON { console = padConsole; })
      } $out/share/gotg/pads.json
    ''}
  ''
