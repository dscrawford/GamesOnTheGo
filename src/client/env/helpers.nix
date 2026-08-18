# Shared shapes for environments, handed to every platform and game file as
# `helpers`. Anything here is a pattern more than one game needs; a setting only
# one game wants belongs in that game's file.
{ pkgs, lib }:

let
  # The step vocabulary recipes compose from; see steps.nix for the contract.
  steps = import ./steps.nix { inherit pkgs; };

  # A scene release, client-side: verify the sfv when one exists, unrar the
  # volume set, keep the largest extracted file as the game. What execute.py
  # used to do on the server, now once per machine on first install.
  sceneArchiveRecipe = {
    scene_archive = [
      steps.verifySfv
      steps.unrar
      steps.pickLargest
      steps.keepExtension
    ];
  };

  # A disc image that travelled as a 7z: extract, convert to the RVZ the
  # emulator wants, keep nothing else. Space cost is transient (staging holds
  # the raw image); the refined artifact is deterministic, so the raw members
  # are the client's to delete afterwards.
  discArchiveRecipe = {
    single_archive = [
      steps.extract7z
      steps.pickLargest
      steps.convertRvz
    ];
  };

in
{
  inherit steps sceneArchiveRecipe discArchiveRecipe;

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
        # A launch from the sofa goes straight to the game; the windowed UI is
        # one Esc away when wanted.
        "--fullscreen"
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

      # Disc images that travelled as archives are extracted and converted to
      # RVZ here, on first install — the pipeline inversion's client half.
      recipes = discArchiveRecipe;

      args = [
        "-b"
        "-e"
        "{target}"
      ];
      preLaunch = ''
        # Dolphin writes these files itself and records only what differs from a
        # default, so there is usually no line to replace and often no section
        # either — which is why this sets a key rather than substituting one.
        gotg_ini_set() {
          gotg_ini_file="$1"
          gotg_ini_section="[$2]"
          gotg_ini_key="$3"
          gotg_ini_value="$4"

          mkdir -p "$(dirname "$gotg_ini_file")"
          touch "$gotg_ini_file"
          ${pkgs.gawk}/bin/awk \
            -v section="$gotg_ini_section" \
            -v key="$gotg_ini_key" \
            -v value="$gotg_ini_value" '
            BEGIN { line = key " = " value }
            # Leaving the section without having written the key: write it now,
            # ahead of the header that ends the section.
            /^\[/ {
              if (inSection && !written) { print line; written = 1 }
              inSection = ($0 == section)
            }
            inSection && index($0, key " ") == 1 { print line; written = 1; next }
            { print }
            END {
              if (!written) {
                if (!inSection) print section
                print line
              }
            }
          ' "$gotg_ini_file" > "$gotg_ini_file.gotg" &&
            mv "$gotg_ini_file.gotg" "$gotg_ini_file"
        }

        # Only where this environment owns its Dolphin configuration. Otherwise
        # this would be reaching into the settings of the player's own Dolphin
        # install, which is not ours to change. Tested at runtime rather than
        # against `isolate` here, so that a game file turning isolation on gets
        # this too.
        if [ "''${XDG_CONFIG_HOME:-}" = "$state/config" ]; then
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Input BackgroundInput True

          # Straight to the game, full screen; -b already skips the UI.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Display Fullscreen True

          # Vulkan rather than Dolphin's OpenGL default: the Mesa/RDNA2
          # handhelds this targets run markedly faster on it.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Core GFXBackend Vulkan

          # What reads as "a little slow" is usually shader compilation, not
          # throughput. Mode 2 (hybrid ubershaders) draws through the
          # ubershader while the specialized shader compiles in the
          # background, and the warm-up compile clears the cached backlog
          # before the game starts instead of as stutter inside it.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/GFX.ini" \
            Settings ShaderCompilationMode 2
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/GFX.ini" \
            Settings WaitForShadersBeforeStarting True

          # Internal resolution, from the player's own preference rather than
          # this flake: what looks right depends on the screen in front of you,
          # which is not something a derivation can know.
          #
          # Dolphin scales by whole multiples of the GameCube's 640x528, and
          # labels them by the width that lands on: 640*N wide, so 3x is 1080p
          # and 4K is 6x. Read off Dolphin's own label format, not guessed.
          gotg_res=default
          if [ -f "$GOTG_USER_CONFIG/video.json" ]; then
            gotg_res="$(${pkgs.jq}/bin/jq -r '.resolution // "default"' \
              "$GOTG_USER_CONFIG/video.json" 2>/dev/null || echo default)"
          fi

          gotg_scale=""
          case "$gotg_res" in
            default | native) gotg_scale="" ;;
            720p) gotg_scale=2 ;;
            1080p) gotg_scale=3 ;;
            1440p) gotg_scale=4 ;;
            4k | 4K) gotg_scale=6 ;;
            5k | 5K) gotg_scale=8 ;;
            [1-8]x) gotg_scale="''${gotg_res%x}" ;;
            *)
              echo "gotg: unknown resolution '$gotg_res' in $GOTG_USER_CONFIG/video.json" >&2
              echo "gotg: expected default, 720p, 1080p, 1440p, 4k, 5k, or 1x-8x" >&2
              ;;
          esac

          # "default" writes nothing at all, deliberately. It means this is not
          # ours to manage, so whatever Dolphin or the player set in its own
          # settings screen survives — rather than being reset on every launch.
          if [ -n "$gotg_scale" ]; then
            gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/GFX.ini" \
              Settings InternalResolution "$gotg_scale"
          fi
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

  # Luigi's Mansion 2 HD's frame-rate patch, shared by the variants that differ
  # only in the emulated refresh rate they pair it with.
  #
  # Pinned to a commit rather than a branch on purpose: it is two lines of
  # machine code written against one build of one game, so a silent change
  # upstream would be a silent change to the game's timing.
  #
  # It writes `mov r1, #1` over the swap interval — present one frame per
  # vblank. The sibling "FPS Unlocked" patch writes #0 instead, removing the cap
  # entirely, which is how people overshoot into the stairs bug; it is not used.
  luigisMansion2FpsPatch = pkgs.fetchurl {
    url =
      "https://raw.githubusercontent.com/StevensND/switch-port-mods/"
      + "774e7f7c85561ddd865c1cab555f2a335054d522/"
      + "Luigi%27s%20Mansion%202%20HD/%5B010048701995E000%5D/60FPS/1.0.0.pchtxt";
    hash = "sha256-CFxkNqG9m+FvzMK0/ECw6qK4HC8hzuJu+Qee3OwIMLg=";
  };

  # A Ryujinx mod, together with the VSync mode it wants.
  #
  # Ryujinx reads mods from mods/contents/<title id>/<name>/exefs — those three
  # names read off its own HLE assembly, not guessed — and a pchtxt is named for
  # the game version it patches, so a 1.0.0 patch simply does nothing to a later
  # revision rather than breaking it.
  #
  # VSync travels with the mod because the two decide the frame rate *together*.
  # A swap-interval patch says "present one frame per vblank", so whatever the
  # emulated refresh rate is set to becomes the frame rate. Separating them
  # would leave either half looking broken on its own.
  #
  # Pinned on every launch rather than seeded once: F1 changes the mode at
  # runtime, and without this a stray press would persist into the next session.
  #
  # vsyncMode is 0 Switch (60Hz), 1 Unbounded, 2 Custom — measured by starting
  # Ryujinx against each value and reading back what it logged.
  ryujinxMod =
    {
      titleId,
      name,
      patch,
      gameVersion ? "1.0.0",
      vsyncMode,
      customInterval ? null,
    }:
    let
      custom = customInterval != null;
      edits = [
        ".vsync_mode = ${toString vsyncMode}"
        ".enable_custom_vsync_interval = ${if custom then "true" else "false"}"
      ] ++ lib.optional custom ".custom_vsync_interval = ${toString customInterval}";
    in
    {
      preLaunch = ''
        mods="$XDG_CONFIG_HOME/Ryujinx/mods/contents/${titleId}/${name}/exefs"
        if [ ! -f "$mods/${gameVersion}.pchtxt" ]; then
          mkdir -p "$mods"
          cp --no-preserve=mode ${patch} "$mods/${gameVersion}.pchtxt"
          echo "installed the ${name} patch" >&2
        fi

        # The platform's own preLaunch runs first and generates the default
        # config on a first run — see switch.nix — so by here there is one to
        # edit, and the fallback below is only for a game file that stopped
        # composing over the platform base.
        config="$XDG_CONFIG_HOME/Ryujinx/Config.json"
        if [ -f "$config" ]; then
          if ${pkgs.jq}/bin/jq '${lib.concatStringsSep " | " edits}' \
            "$config" >"$config.gotg"; then
            mv "$config.gotg" "$config"
          else
            rm -f "$config.gotg"
          fi
        else
          echo "gotg: no Ryujinx config yet; the frame rate applies next launch" >&2
        fi
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
