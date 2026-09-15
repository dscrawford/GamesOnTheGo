# Ryujinx. Split out of helpers.nix: what each emulator needs to be driven
# correctly is its own body of knowledge, and they were only ever neighbours in
# one file.
{ pkgs, lib }:
{
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
      ]
      ++ lib.optional custom ".custom_vsync_interval = ${toString customInterval}";
    in
    {
      preLaunch = ''
        mods="$XDG_CONFIG_HOME/Ryujinx/mods/contents/${titleId}/${name}/exefs"
        if [ ! -f "$mods/${gameVersion}.pchtxt" ]; then
          mkdir -p "$mods"
          cp --no-preserve=mode ${lib.escapeShellArg patch} "$mods/${gameVersion}.pchtxt"
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

}
