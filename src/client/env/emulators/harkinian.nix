# The HarbourMasters native ports. Split out of helpers.nix: what each of these
# needs to be driven correctly is its own body of knowledge, and they were only
# ever neighbours in one file.
{ lib, steps }:
{
  # The HarbourMasters ports — Ship of Harkinian, 2 Ship 2 Harkinian — are native
  # ports rather than emulators, and take the ROM differently from anything else
  # here. They read it once, extract it into an .o2r archive kept beside their
  # settings, and never look at it again; the game itself launches with no
  # arguments at all.
  #
  # So the ROM is a first-run bootstrap, not a command line. Passing it every
  # time would be worse than useless: with an archive already present the port
  # stops on a "Confirm Re-extract" prompt, and then on "All files have been
  # processed. Run SoH?" — two dialogs, on every launch, forever.
  #
  #   port      the package
  #   bin       the binary inside it
  #   appName   what it calls itself to SDL_GetPrefPath, which is where the
  #             archive lands: nixpkgs builds these NON_PORTABLE, so their data
  #             directory is $XDG_DATA_HOME/<appName> rather than the (read-only)
  #             store path they were installed to
  #   archives  the archive names that mean "already bootstrapped"; any one of
  #             them is enough, since a Master Quest ROM produces oot-mq.o2r
  #             where a retail one produces oot.o2r
  harkinianPort =
    {
      port,
      bin,
      appName,
      archives,
    }:
    {
      emulator = port;
      inherit bin;
      # These have no launcher to open: their settings are inside the game, and
      # starting them with no arguments starts the game — which `gotg play`
      # already does.
      configurable = false;

      # The ports read a bare .z64, not the No-Intro zip it travels as. The
      # matching half is data/overrides.json marking the entry `unzip`: where
      # the unpacked tree lands has to be known without evaluating nix for
      # `gotg list` to work offline; this is what fills it. Both handlers,
      # because the same zip is single_file from the games-root pass and
      # no_intro_set from the DAT torrent path — one re-import apart.
      recipes =
        let
          unpacked = [
            steps.unzip
            steps.placeTree
          ];
        in
        {
          single_file = unpacked;
          no_intro_set = unpacked;
        };
      # The archive is derived from the ROM and worth keeping with the game
      # rather than in a shared ~/.local/share, so that removing a game removes
      # everything it made. Saves live here too.
      isolate = true;
      args = [ ];

      # These ports were already writing under {state}, so naming their saves
      # moves nothing — it only says what is worth carrying to another machine.
      # Matched against a live 2ship directory: the saves themselves, and the
      # settings file beside them, which is small and worth keeping in step.
      saves = [
        "data/${appName}/saves/**"
        "data/${appName}/${appName}*.json"
      ];
      # The .o2r is tens of megabytes and is rebuilt from the ROM by the first
      # run on any machine, so uploading it would be paying to move something
      # the other end can make for itself. The rest is noise.
      saveExcludes = [
        "data/${appName}/*.o2r"
        "data/${appName}/logs/**"
        "data/${appName}/mods/**"
        "data/${appName}/imgui.ini"
      ];
      # Where a hand-installed copy of the same port keeps its saves. `adopt`
      # copies from here once, so switching to gotg does not look like losing
      # every file. `isolate` points XDG_DATA_HOME at {state}/data, which is why
      # that is where it goes.
      legacyPaths = [
        {
          from = "$XDG_DATA/${appName}";
          into = "data";
        }
      ];

      preLaunch = ''
        harkinian_data="''${XDG_DATA_HOME:-$HOME/.local/share}/${appName}"
        if ${lib.concatMapStringsSep " && " (a: ''[ ! -e "$harkinian_data/${a}" ]'') archives}; then
          echo "first run: extracting game assets from $target" >&2
          mkdir -p "$harkinian_data"
          # The ports take no ROM argument: they scan their working directory
          # and data directory for one and offer to extract it. Learned on the
          # Deck — every desktop had adopted a hand-made archive through
          # legacyPaths, so the first-run path had never actually run. A copy
          # rather than a symlink: the scan follows neither.
          cp -f "$target" "$harkinian_data/gotg-extract.z64"
          cd "$harkinian_data"
          # Hand the process over rather than coming back here: after
          # extracting, the port offers to start the game, so returning would
          # launch a second copy the moment the player quit the first.
          exec ${port}/bin/${bin}
        fi
        # The staged copy is only needed until the archive exists.
        rm -f "$harkinian_data/gotg-extract.z64"
      '';
    };
}
