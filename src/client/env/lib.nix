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
  # Display name, for the Steam entry and its artwork search. Null falls back
  # to the catalog title — with the variant in parentheses when there is one —
  # so only an environment that wants a better name states one:
  # "Majora's Mask Randomizer" over "Majora's Mask (rando)".
  title ? null,
  # The package the emulator lives in, and which binary inside it to run.
  emulator,
  bin ? null,
  # The command line. Placeholders are substituted at launch: {target} is the
  # ROM, {install} the directory it was downloaded into, {state} this
  # environment's writable directory, and {fullscreen} the flag below — or
  # nothing, when the launch is not one that should take the whole screen.
  args ? [ "{target}" ],
  # What this emulator calls "full screen" on its command line. Only reached
  # through {fullscreen}; an emulator that takes the setting from a config file
  # instead reads $gotg_fullscreen in its preLaunch.
  fullscreenFlag ? "--fullscreen",
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
  # This environment brings up a compositor of its own -- the split-screen
  # sessions do, a nested sway with a gamescope per copy inside it.
  #
  # It is here because such a session cannot run inside padmap's sandbox. That
  # sandbox is a user namespace, and in one of those every file owned by root
  # reads as `nobody`, /tmp/.X11-unix included. wlroots refuses to put an X
  # socket in a directory that is "not owned by root or us", so Xwayland never
  # starts, gamescope is handed no output, and what reaches the screen is
  # black. Nothing in the logs says "sandbox": it says
  #
  #     /tmp/.X11-unix not owned by root or us
  #     No display available in the first 33
  #     Failed to start Xwayland
  #
  # Losing that sandbox costs these sessions nothing, because they already do
  # the same job better: each copy is given its own seat, so a game sees its
  # own pad and no one else's -- see mods/coop-seats.nix.
  ownsSession ? false,
  # This environment runs the game on something other than the platform's
  # emulator -- a native port off a decompilation, in every case so far.
  #
  # Declared rather than guessed, because the two kinds of per-game
  # environment look identical from outside: world.super_metroid.nix is the
  # platform's emulator with settings of its own, and swapping it for the bare
  # platform would only drop those settings. Only where the emulator itself is
  # replaced is there something to swap back to, and that is what `gotg play
  # <id> emulate` offers.
  nativePort ? false,
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
  # The newest version of the game this environment was built against, for an
  # environment that is a mod rather than an emulator. A Switch exefs mod is
  # machine code written against one executable: on a newer update it patches
  # nothing, or patches the wrong thing and crashes minutes later with nothing
  # on screen to say why. Stated here, the client picks the newest update at or
  # below it — and refuses the launch when the library has nothing that old,
  # which is a sentence rather than a mystery.
  gameVersionMax ? null,
  # The oldest, for the same reason in the other direction. A patch that hooks
  # an address a later update introduced finds nothing to hook on an earlier
  # one, and fails exactly the way it fails on a version too new: quietly, and
  # then not quietly. Stating one end without the other says a mod is open in
  # that direction, which is usually true and worth being explicit about.
  gameVersionMin ? null,
  # The ares console section whose controller bindings can be generated —
  # "SuperFamicom", "Nintendo64". Null leaves an environment's bindings alone.
  # ares creates a section the first time that console is run, so the name is
  # not derivable from the platform slug and is only set where it is confirmed.
  padConsole ? null,
  # Whose bindings can be written for this environment. ares needs a console
  # name as well, since its one settings file keeps a section per console;
  # dolphin keeps a file per pad and needs nothing beyond knowing it is dolphin.
  padEmulator ? (if padConsole != null then "ares" else null),
  # What padmap's clones should look like to this environment's program.
  #
  # Null is padmap's default, "mirror": the clone carries the physical pad's
  # vendor and product, which is right nearly everywhere -- an emulator that
  # was told which controller to bind wants to see that controller.
  #
  # "xbox360" makes every clone a wired Microsoft pad, 045e:028e, the one
  # GUID every SDL build maps out of the box. That is for the decompiled
  # ports: they carry their own controller database, and a clone of a Steam
  # Controller is in nobody's. Set it where a port has been seen to need it,
  # not by default -- under it every clone shares one GUID, which Ryujinx
  # cannot tell apart (it blanks the name CRC to make its device id), so
  # padmap.sh refuses it for that emulator rather than seating four players
  # on top of each other.
  padIdentity ? null,
  # Whether `gotg configure` can open this emulator's own settings screen.
  #
  # Defaults to whether the environment isolates, because without isolation
  # there is no separate configuration to open — it would be the player's own
  # install, which is the one thing every other part of this avoids touching.
  # A port whose settings are in-game rather than in a launcher sets this false:
  # opening it would just start the game, which `gotg play` already does.
  configurable ? isolate,
  # Files the emulator needs that are not the game and are not ours to ship:
  # console keys, a BIOS. They live in the platform's own directory on the
  # server, and the client fetches them into {state}/<into> on demand.
  #
  #   { into = "config/Ryujinx/system"; files = [ "prod.keys" ]; }
  #
  # `platform` names whose directory to take them from, for the file that is
  # not this platform's own: a GameCube game emulating Game Boy Advances wants
  # the GBA's BIOS, and there is one copy of that, under gba.
  #
  # Not baked into the derivation on purpose. They are neither redistributable
  # nor stable — keys track console firmware — so a store path holding them
  # would be both wrong and stale.
  keys ? null,
  # Console firmware, for the emulator that stops on an install dialog without
  # it. Same reasoning as keys — not redistributable, so it sits beside them on
  # the server — but hundreds of megabytes, so the client caches it once per
  # platform and hardlinks it into each environment.
  #
  #   { into = "config/Ryujinx/bis/system/Contents/registered"; file = "firmware.zip"; }
  firmware ? null,
  # How this platform turns a raw catalog source into a runnable game — the
  # client side of the pipeline inversion. The catalog says what a source *is*
  # (its handler); this says what to do about it, so the tool closures stay
  # with the platform that needs them. One pipeline of steps per handler,
  # composed from env/steps.nix:
  #
  #   { scene_archive = [ steps.verifySfv steps.unrar steps.pickLargest steps.keepExtension ]; }
  #
  # The generated script runs as `gotg-recipe <handler> <raw-dir> <dest>` with
  # each step tool bound under its name — overridable as GOTG_<name> from the
  # environment, which is what lets tests stub a multi-gigabyte conversion
  # into an echo.
  recipes ? { },
}:

let
  exe = if bin == null then lib.getExe emulator else "${emulator}/bin/${bin}";

  # Quote for the shell, then reopen the quoting around each placeholder: the
  # expansion has to sit outside the single quotes to happen at all, and inside
  # double quotes so a path with spaces stays one argument.
  # `{fullscreen}` is the one placeholder that expands *bare*, and that is the
  # whole point: it has to be able to expand to no argument at all. The match
  # includes the quotes escapeShellArg put around it, because leaving even an
  # empty pair — ''$gotg_fullscreen'' — still forms a word, and ares reads an
  # empty argument as a ROM path. Measured, after writing it the other way.
  #
  # So it is only meaningful as a whole argument; embedded in a longer string
  # it stays literal rather than silently half-working.
  render =
    s:
    lib.replaceStrings
      [ "{target}" "{install}" "{state}" "'{fullscreen}'" ]
      [ "'\"$target\"'" "'\"$install\"'" "'\"$state\"'" "$gotg_fullscreen" ]
      (lib.escapeShellArg s);

  # The directory each saves glob lives in: segments up to the first wildcard,
  # or the dirname of an exact path. An emulator told to save somewhere must
  # find that place existing — ares reports a missing save path as read-only,
  # and the session's progress is lost with it.
  savesDirPrefix =
    glob:
    let
      segments = lib.splitString "/" glob;
      isWild = s: builtins.match ".*[*?[].*" s != null;
      # take-while, by fold: the pinned nixpkgs has no lib.lists.takeWhile.
      static =
        (lib.foldl'
          (
            acc: s:
            if acc.stop || isWild s then
              acc // { stop = true; }
            else
              {
                stop = false;
                segs = acc.segs ++ [ s ];
              }
          )
          {
            stop = false;
            segs = [ ];
          }
          segments
        ).segs;
      dirs = if lib.length static == lib.length segments then lib.init segments else static;
    in
    lib.concatStringsSep "/" dirs;

  savesDirs = lib.unique (lib.filter (d: d != "") (map savesDirPrefix saves));

  seedSavesDirs = lib.concatMapStringsSep "\n" (
    d: ''mkdir -p "$state"/${lib.escapeShellArg d}''
  ) savesDirs;

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

  # GL on a machine that is not NixOS: see foreign-gl.nix, which is also
  # what the picker and the QA tools use.
  foreignGl = (import ./foreign-gl.nix { inherit (pkgs) mesa; }).guarded;

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
  }
  // lib.optionalAttrs (title != null) { inherit title; }
  // lib.optionalAttrs (gameVersionMax != null) { inherit gameVersionMax; }
  // lib.optionalAttrs (gameVersionMin != null) { inherit gameVersionMin; };

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
      ${seedSavesDirs}

      # The player's own configuration directory, captured *before* isolation
      # moves XDG_CONFIG_HOME under {state}. Preferences that belong to the person
      # rather than to the environment are read from here, so one setting can
      # apply across every environment without being baked into any of them.
      export GOTG_USER_CONFIG="''${GOTG_USER_CONFIG:-''${XDG_CONFIG_HOME:-$HOME/.config}/gotg}"
      ${lib.optionalString isolate ''
        export XDG_CONFIG_HOME="$state/config"
        export XDG_DATA_HOME="$state/data"
        mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
      ''}
      ${seedConfig}
      ${exports}
      ${foreignGl}
      # Full screen belongs to *how a game was started*, not to the game.
      #
      # Steam gives a launcher no terminal — both streams land in a log file, the
      # same test color.sh uses to decide there is nobody to colour output for —
      # and somebody launching that way is on a sofa or a handheld and wants the
      # game and nothing else. A terminal means a person at a desk with other
      # windows open, and taking the whole desktop is rude at best. On a
      # multi-monitor desktop it is worse than rude: ares spans every screen and
      # draws on none of them.
      #
      # GOTG_FULLSCREEN forces it either way, and `gotg play <id> --fullscreen`
      # still works because runtime arguments are appended after these.
      #
      # SC2034 is disabled on both statements: the variable is consumed through
      # the {fullscreen} placeholder or a preLaunch *when the environment names
      # it*, and a port that owns its display — the Harkinian games, sm64coopdx —
      # has no flag to substitute, so in its launcher it really is unread. The
      # build runs the linter, so without the directives every such environment
      # fails to build at all. Found by a Master Quest launch on somebody else's
      # machine. (And no comment line here may begin with the linter's own name,
      # which it reads as a directive and refuses to parse — found the same way.)
      # shellcheck disable=SC2034
      gotg_fullscreen=""
      # shellcheck disable=SC2034
      case "''${GOTG_FULLSCREEN:-}" in
        1 | true | yes | on) gotg_fullscreen=${lib.escapeShellArg fullscreenFlag} ;;
        0 | false | no | off) gotg_fullscreen="" ;;
        *) [ -t 1 ] || gotg_fullscreen=${lib.escapeShellArg fullscreenFlag} ;;
      esac

      ${preLaunch}
      exec ${exe} ${lib.concatMapStringsSep " " render args} "$@"
    '';
  };
  # Every tool any step in any pipeline names, deduplicated. Two steps naming
  # one variable must mean the same binary — asserted, not assumed, because a
  # hand-written step could shadow a library one silently.
  recipeTools = lib.foldl' (
    acc: step:
    lib.foldlAttrs (
      a: var: bin:
      assert lib.assertMsg (
        !(a ? ${var}) || a.${var} == bin
      ) "gotg-recipe: tool ${var} is pinned to two different binaries";
      a // { ${var} = bin; }
    ) acc (step.tools or { })
  ) { } (lib.concatLists (lib.attrValues recipes));
  recipeApp = lib.optionalAttrs (recipes != { }) {
    drv = pkgs.writeShellApplication {
      name = "gotg-recipe";
      text = ''
        # usage: gotg-recipe <handler> <raw-dir> <dest>
        handler="''${1:?usage: gotg-recipe <handler> <raw-dir> <dest>}"
        raw="''${2:?raw member directory required}"
        dest="''${3:?destination required}"
        ${lib.concatLines (
          # Assigned, never exported: Info-ZIP's unzip reads an UNZIP variable
          # in its environment as prepended arguments, and other tools have
          # conventions like it. Only the script itself expands these. The
          # unset matters — a variable arriving already exported keeps its
          # export attribute through a plain reassignment.
          lib.mapAttrsToList (var: default: ''
            unset ${var}
            ${var}="''${GOTG_${var}:-${default}}"
          '') recipeTools
        )}
        step=dispatch
        fail() {
          echo "gotg-recipe[$step]: $*" >&2
          exit 1
        }
        # One scratch dir for the whole pipeline, swept on any exit — a failed
        # step must not leave debris where the games live. mktemp rather than
        # $$: a guessable name in a shared directory is a symlink race.
        mkdir -p "$(dirname "$dest")"
        stage="$(mktemp -d "$(dirname "$dest")/.gotg-recipe-XXXXXXXX")"
        trap 'rm -rf "$stage"' EXIT
        # The cursor: what the pipeline has made so far. Starts as the raw
        # member directory; each step leaves it pointing at its own output.
        cur="$raw"
        case "$handler" in
        ${lib.concatLines (
          lib.mapAttrsToList (handler: steps: ''
            ${handler})
              ${lib.concatMapStringsSep "\n" (step: ''
                step="${step.name}"
                ${step.script}
              '') steps}
              ;;
          '') recipes
        )}
          *) fail "no recipe for $handler" ;;
        esac
      '';
    };
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
    ${lib.optionalString ownsSession ''
      touch $out/share/gotg/owns-session
    ''}
    ${lib.optionalString nativePort ''
      touch $out/share/gotg/native-port
    ''}
    ${lib.optionalString (keys != null) ''
      cp ${pkgs.writeText "keys.json" (builtins.toJSON keys)} $out/share/gotg/keys.json
    ''}
    ${lib.optionalString (firmware != null) ''
      cp ${pkgs.writeText "firmware.json" (builtins.toJSON firmware)} $out/share/gotg/firmware.json
    ''}
    ${lib.optionalString (recipes != { }) ''
      ln -s ${recipeApp.drv}/bin/gotg-recipe $out/bin/gotg-recipe
      cp ${
        pkgs.writeText "recipe.json" (builtins.toJSON { handlers = lib.attrNames recipes; })
      } $out/share/gotg/recipe.json
    ''}
    ${lib.optionalString configurable ''
      cp ${
        pkgs.writeText "configure.json" (
          builtins.toJSON {
            exec = exe;
            # The same environment a launch runs under. Without it a settings
            # screen sees a different machine than the game does — the SDL hints
            # in particular decide whether a controller exists at all, so
            # binding a pad in a configure that lacked them would be binding a
            # pad the game will not have.
            env = baseEnv // env;
          }
        )
      } $out/share/gotg/configure.json
    ''}
    ${lib.optionalString (padEmulator != null || padIdentity != null) ''
      cp ${
        pkgs.writeText "pads.json" (
          builtins.toJSON (
            {
              # Which configuration directory the writer should be editing.
              # Cemu's lives under XDG_CONFIG_HOME, which isolation moves — and
              # on a first launch neither location exists yet, so this cannot be
              # settled by looking.
              inherit isolate;
            }
            // lib.optionalAttrs (padEmulator != null) { emulator = padEmulator; }
            // lib.optionalAttrs (padConsole != null) { console = padConsole; }
            // lib.optionalAttrs (padIdentity != null) { identity = padIdentity; }
          )
        )
      } $out/share/gotg/pads.json
    ''}
  ''
