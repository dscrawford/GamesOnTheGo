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
