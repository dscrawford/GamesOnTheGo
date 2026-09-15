# Ryujinx. Split out of helpers.nix: what each emulator needs to be driven
# correctly is its own body of knowledge, and they were only ever neighbours in
# one file.
{ pkgs, lib }:
let
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
      edits = [
        ".vsync_mode = ${toString vsyncMode}"
        ".enable_custom_vsync_interval = ${if custom then "true" else "false"}"
      ]
      ++ lib.optional custom ".custom_vsync_interval = ${toString customInterval}";
    in
    ''
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
in
{
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
  # mod — so it is written only when there is none, leaving both a second mod's
  # cheats and the player's own choices in Ryujinx's cheat manager alone.
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
      + lib.optionalString (enabledCheats != null) ''
        if [ ! -f "$contents/cheats/enabled.txt" ]; then
          mkdir -p "$contents/cheats"
          cp --no-preserve=mode ${lib.escapeShellArg enabledCheats} \
            "$contents/cheats/enabled.txt"
          echo "switched the ${name} cheats on" >&2
        fi
      ''
      + vsync { inherit vsyncMode customInterval; };
    };

}
