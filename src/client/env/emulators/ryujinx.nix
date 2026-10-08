# Ryujinx.
{ pkgs, lib }:
let
  inherit (import ./jq-edit.nix { inherit pkgs; }) gotgJqEdit;

  # One or more jq edits against the generated config. Pulled out of vsync
  # below because the frame rate is not the only machine-side setting a game
  # needs: Paper Mario's resolution mods want more emulated memory than the
  # console has, and crash without it.
  configEdit =
    edits:
    ''
      # The platform's own preLaunch runs first and generates the default
      # config on a first run — see switch.nix — so by here there is one to
      # edit, and the fallback below is only for a game file that stopped
      # composing over the platform base.
      config="$XDG_CONFIG_HOME/Ryujinx/Config.json"
      if [ -f "$config" ]; then
        ${gotgJqEdit {
          file = "$config";
          filter = lib.concatStringsSep " | " edits;
          indent = "  ";
        }}
      else
        echo "gotg: no Ryujinx config yet; this applies next launch" >&2
      fi
    '';

  # The lines of `enabled` that enabled.txt lacks, appended to it. See
  # ryujinxModDir's note on enabledCheats.
  #
  # grep without the game's LD_PRELOAD: the launch exports sdl2-compat's
  # libSDL2 for Ryujinx (switch.nix), and preloaded into a program with no
  # SDL3 beside it that library prints "Failed loading SDL3 library" and
  # aborts -- grep, cmp, anything from PATH; the store's jq happens to
  # survive it, which is why the config edits never showed the problem.
  cheatsOn = name: enabled: ''
    mkdir -p "$contents/cheats"
    touch "$contents/cheats/enabled.txt"
    while IFS= read -r cheat; do
      [ -n "$cheat" ] || continue
      if ! LD_PRELOAD=''' grep -qxF -- "$cheat" "$contents/cheats/enabled.txt"; then
        printf '%s\n' "$cheat" >>"$contents/cheats/enabled.txt"
        echo "switched a ${name} cheat on" >&2
      fi
    done <${lib.escapeShellArg enabled}
  '';

  # The half of a mod that is not files: what frame rate the emulated display
  # is willing to run at.
  #
  # VSync travels with the mod because the two decide the frame rate
  # *together*. A swap-interval patch says "present one frame per vblank", so
  # whatever the emulated refresh rate is set to becomes the frame rate.
  # Separating them would leave either half looking broken on its own.
  #
  # Pinned on every launch rather than seeded once: F1 changes the mode at
  # runtime, and without this a stray press would persist into the next session.
  #
  # vsyncMode is 0 Switch (60Hz), 1 Unbounded, 2 Custom — measured by starting
  # Ryujinx against each value and reading back what it logged.
  vsync =
    {
      vsyncMode,
      customInterval ? null,
    }:
    let
      custom = customInterval != null;
    in
    configEdit (
      [
        ".vsync_mode = ${toString vsyncMode}"
        ".enable_custom_vsync_interval = ${if custom then "true" else "false"}"
      ]
      ++ lib.optional custom ".custom_vsync_interval = ${toString customInterval}"
    );
in
{
  # A game's variant file, with a mod's preLaunch added after the platform's
  # and the game-version window the mod states carried along. Five files
  # (the TOTK and BotW UltraCam variants) each wrote `preLaunch = (base.preLaunch
  # or "") + (mod).preLaunch` and, beside it, the same two `gameVersion*`
  # lines for their mod -- the window belongs to the mod's executable patch,
  # so it travels in the mod's `window` and is stated once where the reason
  # for it is (totk-ultracam.nix, botw-ultracam.nix). `base // patch`
  # *replaces* preLaunch, which is why this appends rather than assigns
  # (checks.inheritsPlatform).
  withMod =
    base: mod:
    base
    // (mod.window or { })
    // {
      preLaunch = (base.preLaunch or "") + mod.preLaunch;
    };

  # A Ryujinx mod that is a single executable patch, together with the VSync
  # mode it wants.
  #
  # Ryujinx reads mods from mods/contents/<title id>/<name>/exefs — those three
  # names read off its own HLE assembly, not guessed — and a pchtxt is named for
  # the game version it patches, so a 1.0.0 patch simply does nothing to a later
  # revision rather than breaking it.
  ryujinxMod =
    {
      titleId,
      name,
      patch,
      gameVersion ? "1.0.0",
      vsyncMode,
      customInterval ? null,
    }:
    {
      preLaunch = ''
        mods="$XDG_CONFIG_HOME/Ryujinx/mods/contents/${titleId}/${name}/exefs"
        if [ ! -f "$mods/${gameVersion}.pchtxt" ]; then
          mkdir -p "$mods"
          cp --no-preserve=mode ${lib.escapeShellArg patch} "$mods/${gameVersion}.pchtxt"
          echo "installed the ${name} patch" >&2
        fi

        ${vsync { inherit vsyncMode customInterval; }}
      '';
    };

  # How much memory the emulated console has. The Switch has 4GiB and that is
  # the default; a game asking for more is a game running mods the hardware was
  # never expected to run. Paper Mario's in-engine resolution mods are one:
  # at the stock size the game dies about ten seconds in with an invalid
  # access at address zero, and at 8GiB it runs.
  #
  # 0 is 4GiB, 1 is 6GiB, 2 is 8GiB, 3 is 12GiB -- read off the emulator's own
  # MemoryConfiguration enum rather than guessed.
  #
  # Not on a Deck. Its 16 GB are shared with the GPU and the texture cache
  # grows with the emulated DRAM (Ryubing 1.2.67), so the platform keeps the
  # console's 4 GiB there (switch.nix) and the mod that needed more is the
  # one left out on a Deck (ryujinxModOnly's onDeck).
  ryujinxDram = size: {
    preLaunch = ''
      if [ "''${GOTG_MACHINE:-}" != deck ]; then
        ${configEdit [ ".dram_size = ${toString size}" ]}
      fi
    '';
  };

  # A mod directory and nothing else: no frame rate, no memory. For a game
  # that installs several, where saying the same vsync three times would be
  # three chances to say it differently.
  #
  # onDeck = false is a mod a Deck cannot afford -- a 1080p render on a
  # 1280x800 panel that also wants 8 GiB of emulated DRAM -- and on a Deck it
  # is removed if an earlier launch put it there, since mods live in the
  # machine's own state and an installed one stays installed.
  ryujinxModOnly =
    {
      titleId,
      name,
      dir,
      onDeck ? true,
      enabledCheats ? null,
    }:
    {
      preLaunch = ''
        contents="$XDG_CONFIG_HOME/Ryujinx/mods/contents/${titleId}"
        if ${if onDeck then "false" else ''[ "''${GOTG_MACHINE:-}" = deck ]''}; then
          if [ -d "$contents/${name}" ]; then
            rm -rf "$contents/${name}"
            echo "removed the ${name} mod: not for a Deck" >&2
          fi
        elif [ ! -d "$contents/${name}" ]; then
          mkdir -p "$contents/${name}"
          cp -R --no-preserve=mode ${lib.escapeShellArg dir}/. "$contents/${name}/"
          echo "installed the ${name} mod" >&2
        fi
      ''
      + lib.optionalString (enabledCheats != null) (cheatsOn name enabledCheats);
    };

  # The same, for a mod that arrives as a directory rather than a lone patch.
  #
  # Ryujinx walks everything under mods/contents/<title id>/ and recognises
  # three names wherever it finds them: exefs (executable patches), romfs
  # (replaced game files) and cheats (runtime toggles). A mod that ships all
  # three has to be installed whole — handing over only its pchtxt would leave
  # the half that is data behind, and several of these patches replace a game
  # file *and* the code that reads it.
  #
  # Every version file the archive ships is kept, not just the one matching the
  # dump here. Ryujinx matches a pchtxt to the running executable by the build
  # id inside it (@nsobid), so the files for other versions are inert, and
  # keeping them means an update to the game does not silently turn the mod off.
  #
  # enabledCheats, when given, is the file listing which of those cheats start
  # switched on. Ryujinx reads that list from one place per game rather than per
  # mod, so the lines are merged in: a cheat a variant ships on is on -- the 60
  # FPS one is the way out of a minigame, the text-speed one a hotkey that does
  # nothing until pressed -- and every other line, a second mod's or the
  # player's own in Ryujinx's cheat manager, is left as it was. (It used to be
  # written only when the file was missing, which left a cheat added later off
  # on every machine that had launched the game before.)
  ryujinxModDir =
    {
      titleId,
      name,
      dir,
      enabledCheats ? null,
      vsyncMode,
      customInterval ? null,
    }:
    {
      preLaunch = ''
        contents="$XDG_CONFIG_HOME/Ryujinx/mods/contents/${titleId}"
        if [ ! -d "$contents/${name}" ]; then
          mkdir -p "$contents/${name}"
          cp -R --no-preserve=mode ${lib.escapeShellArg dir}/. "$contents/${name}/"
          echo "installed the ${name} mod" >&2
        fi
      ''
      + lib.optionalString (enabledCheats != null) (cheatsOn name enabledCheats)
      + vsync { inherit vsyncMode customInterval; };
    };
}
