# Shared shapes for environments, handed to every platform and game file as
# `helpers`. Anything here is a pattern more than one game needs; a setting only
# one game wants belongs in that game's file.
{ pkgs, lib }:

{
  # ares, for the cartridge platforms that share it.
  #
  # It does not consult XDG for saves. Emulator::locate reads settings.paths.saves,
  # which is empty by default, and falls back to the ROM's own path with the
  # extension swapped — so a memory save lands in ~/Games next to the ROM, where
  # env-snes and env-snes-world_super_metroid would also be writing over each
  # other. Paths/Saves is that setting, and **the trailing slash is load-bearing**:
  # ares concatenates it with the filename without inserting a separator.
  #
  # ares restores command-line overrides before it saves settings, so passing
  # this never rewrites the user's own settings.bml.
  #
  # The platforms share this because what they share is the part that has to be
  # right. A platform that needs to differ stops calling this and says so.
  aresPlatform =
    {
      platform,
      # What ares calls this console. It does not write the save directly under
      # Paths/Saves — it makes a directory of this name there and puts it
      # inside. Confirmed by launching: a SNES save landed in
      # "{state}/saves/Super Famicom/", and ares keeps a matching
      # "Super Famicom.sys" beside its settings.
      #
      # So this is what an older save has to be adopted *into*. Get it wrong and
      # the copy lands somewhere ares never looks, which from the sofa is
      # indistinguishable from having lost the save — so a platform whose name
      # has not been confirmed by launching adopts nothing at all rather than
      # guessing at it.
      system ? null,
    }:
    {
      emulator = pkgs.ares;
      bin = "ares";
      isolate = true;
      args = [
        "--setting"
        "Paths/Saves={state}/saves/"
        "{target}"
      ];
      saves = [ "saves/**" ];
      # Save states are tied to the ares that wrote them, so one carried from
      # another machine may simply refuse to load. Memory saves always travel.
      saveExcludes = [ "saves/*.bs[0-9]" ];
      # Where ares put them before it was told otherwise: beside the ROM, named
      # for it. `gotg saves adopt` copies these forward, into the directory ares
      # will actually read them from.
      legacyPaths = lib.optionals (system != null) (
        map
          (ext: {
            from = "$GAMES/${platform}/*.${ext}";
            into = "saves/${system}";
          })
          [
            "ram"
            "eeprom"
            "flash"
            "rtc"
            "iram"
            "bsx"
            "dram"
          ]
      );
    };

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
          # Hand the process over rather than coming back here. Given a ROM the
          # port extracts it and then offers to start the game, so returning
          # would launch a second copy the moment the player quit the first.
          exec ${port}/bin/${bin} "$target"
        fi
      '';
    };
}
