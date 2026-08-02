# Shared shapes for environments, handed to every platform and game file as
# `helpers`. Anything here is a pattern more than one game needs; a setting only
# one game wants belongs in that game's file.
{ pkgs, lib }:

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
      # every file.
      legacyPaths = [ "$XDG_DATA/${appName}" ];

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
