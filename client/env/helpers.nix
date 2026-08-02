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
      # The ares console section this platform's games appear under. Only set
      # where it has been read off a real settings.bml — ares creates the
      # section on first run, so it cannot be derived from the platform slug,
      # and a wrong name would write bindings nothing ever reads.
      console ? null,
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
      padConsole = console;
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

  # dolphin, for the two disc platforms that share it.
  #
  # Two things here are about input rather than emulation, and both were learned
  # from a pad that worked in Dolphin's own configuration screen and did nothing
  # in the game:
  #
  # `-b` (batch) starts the game with no library window. Without it Dolphin opens
  # two windows, and only the render one reads a controller — so whichever window
  # the desktop happened to focus decided whether the pad did anything. The
  # configuration screen reads input regardless, which is what makes this look
  # like a binding fault when it is a focus fault.
  #
  # BackgroundInput is the setting that stops focus mattering at all. It is the
  # ini key; "Background Input" is only its label in the interface. Dolphin
  # writes this file itself, so it is edited in place on each launch rather than
  # seeded through configFiles, which only ever fills in a file that is absent.
  dolphinPlatform =
    { }:
    {
      emulator = pkgs.dolphin-emu;
      bin = "dolphin-emu";

      # Isolated because both of the input fixes below need this environment to
      # own its Dolphin configuration — bindings written into the player's own
      # install would be ours to get wrong on their behalf.
      isolate = true;

      # Which follows from isolating: Dolphin's data directory moves under
      # {state}, so a memory card written before this would otherwise be in a
      # directory nothing reads any more, which from the sofa is exactly what
      # losing it looks like. `gotg saves adopt` copies them forward.
      saves = [
        "data/dolphin-emu/GC/**"
        "data/dolphin-emu/Wii/**"
      ];
      legacyPaths = [
        {
          from = "$XDG_DATA/dolphin-emu/GC";
          into = "data/dolphin-emu";
        }
        {
          from = "$XDG_DATA/dolphin-emu/Wii";
          into = "data/dolphin-emu";
        }
      ];

      # GameCube pads only. A Wii game played with a GameCube controller is
      # covered; Wii remotes live in a different file with a different shape,
      # and nothing here pretends to write them.
      padEmulator = "dolphin";

      args = [
        "-b"
        "-e"
        "{target}"
      ];
      preLaunch = ''
        # Only where this environment owns its Dolphin configuration. Otherwise
        # this would be reaching into the settings of the player's own Dolphin
        # install, which is not ours to change. Tested at runtime rather than
        # against `isolate` here, so that a game file turning isolation on gets
        # this too.
        if [ "''${XDG_CONFIG_HOME:-}" = "$state/config" ]; then
          dolphin_ini="$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini"
          mkdir -p "$(dirname "$dolphin_ini")"
          touch "$dolphin_ini"
          ${pkgs.gawk}/bin/awk '
            # Leaving the section without having written it: write it now, ahead
            # of the header that ends the section.
            /^\[/ {
              if (in_input && !written) { print "BackgroundInput = True"; written = 1 }
              in_input = ($0 == "[Input]")
            }
            in_input && /^[ \t]*BackgroundInput[ \t]*=/ {
              print "BackgroundInput = True"; written = 1; next
            }
            { print }
            END {
              if (!written) {
                if (!in_input) print "[Input]"
                print "BackgroundInput = True"
              }
            }
          ' "$dolphin_ini" > "$dolphin_ini.gotg" && mv "$dolphin_ini.gotg" "$dolphin_ini"
        fi
      '';
    };

  # The official BetterSunshineEngine release. Both Sunshine variants install it:
  # BSMSO ships its own copy of the module, byte-identical to this one, and names
  # BSE its "required parent".
  betterSunshineEngine = pkgs.fetchzip {
    url = "https://github.com/DotKuribo/BetterSunshineEngine/releases/download/v4.0.0/BetterSunshineEngine_RELEASE.zip";
    hash = "sha256-haAhVj5sg/xpLXhWDphBFAgeJp89bhxR4N1KR4nFedw=";
    stripRoot = true;
  };

  # Super Mario Sunshine carrying Kuribo modules — shared by the `bse` and
  # `bsmso` variants, which differ only in which files go onto the disc.
  #
  # A Kuribo mod is not a launcher setting: it changes the game's own files. So
  # this opens the disc image, writes the mod in and builds the image back up,
  # keeping the result beside the game's saves. The download in ~/Games is never
  # touched, and removing a mod is deleting one directory.
  #
  # `install` is a shell fragment run with $root at the extracted disc, which is
  # the sys/ and files/ pair both mod READMEs are written in terms of. It must
  # copy with --no-preserve=mode: everything it draws on comes out of the store
  # read-only, and a mod that writes *into* a directory an earlier line copied
  # from there — a module joining Kuribo!/Mods, say — fails on permissions
  # otherwise.
  kuriboSunshineDisc =
    {
      gotgPkgs,
      cache,
      install,
    }:
    {
      path = [
        pkgs.dolphin-emu # dolphin-tool: converts between disc formats
        gotgPkgs.pyisotools # extracts and rebuilds the disc itself
      ];
      preLaunch = ''
        modded="$state/${cache}"
        patched="$modded/super_mario_sunshine.rvz"

        if [ ! -f "$patched" ]; then
          echo "first run: installing ${cache} into a copy of the game" >&2
          # An interrupted run can leave a tree that cannot be deleted: entries
          # copied from the store are read-only, and unlinking one needs write
          # permission on the directory holding it. Without this a single failed
          # first run wedges the environment for good, since every later launch
          # fails on the same rm.
          chmod -R u+w "$modded/build" 2>/dev/null || true
          rm -rf "$modded/build" "$modded/game.iso" "$modded/patched.iso"
          mkdir -p "$modded"

          # pyisotools reads a plain ISO, not the RVZ the library stores.
          dolphin-tool convert -f iso -i "$target" -o "$modded/game.iso"
          pyisotools "$modded/game.iso" E --dest "$modded/build"

          # It unpacks into a "root" directory beneath the destination.
          root="$modded/build/root"
          ${install}
          # Everything copied in came out of the store read-only.
          chmod -R u+w "$root"

          # pyisotools rather than wiimms-iso-tools: the latter rebuilt this
          # GameCube game as a *Wii* disc, partition wrapper and all, which
          # Dolphin loaded happily and the console then failed to boot. Both mod
          # READMEs name pyisotools as a supported rebuilder, and a rebuilt image
          # has been booted headless with OSREPORT logging on to watch Kuribo
          # name each module as it loads it — which is the only check that
          # actually distinguishes a working install from a plausible one.
          pyisotools "$root" B --dest "$modded/patched.iso"
          dolphin-tool convert -f rvz -b 131072 -c zstd -l 5 \
            -i "$modded/patched.iso" -o "$patched"
          rm -rf "$modded/build" "$modded/game.iso" "$modded/patched.iso"
        fi

        # Launch the patched copy instead of the download. The wrapper's
        # arguments are written in terms of $target, so redirecting it here is
        # all it takes.
        target="$patched"
      '';
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
