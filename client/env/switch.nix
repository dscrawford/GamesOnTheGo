# Switch — Ryubing, the maintained Ryujinx fork, as nixpkgs dropped the
# original.
#
# Isolated, which is what makes the keys below land somewhere known: Ryujinx
# keeps everything under $XDG_CONFIG_HOME/Ryujinx, and reads its keys from the
# "system" directory inside that. Confirmed by running it against an empty
# config home and reading back the tree it created — Logs, sdcard, system, bis,
# profiles, games.
{ pkgs, ... }:
{
  emulator = pkgs.ryubing;
  bin = "Ryujinx";
  isolate = true;
  args = [ "{target}" ];

  env = {
    # Ryujinx ships its own libSDL2.so — genuine SDL2 2.30.0 — and .NET loads
    # that in preference to anything on the system. Genuine SDL2 cannot see a
    # Steam Controller: it has no evdev node, so it is reachable only through
    # the HIDAPI driver, and SDL2's is not up to it. nixpkgs' SDL2 is
    # sdl2-compat, the SDL2 API on top of SDL3, and SDL3's is.
    #
    # Measured against the pad in question, hint set in every case:
    #
    #   bundled SDL2 2.30.0            1 joystick   (Xbox only)
    #   bundled + this preload         2 joysticks  (Steam Controller + Xbox)
    #
    # Preloading works because both carry the same SONAME: by the time .NET
    # dlopens the bundled path, the loader already has that name resolved and
    # hands back what is loaded. Nothing is patched or replaced on disk.
    LD_PRELOAD = "${pkgs.SDL2}/lib/libSDL2-2.0.so.0";
  };

  # A Switch game will not decrypt without console keys, and they are not ours
  # to ship: they belong to a console, they are not redistributable, and they
  # track firmware, so a copy baked into a derivation would be stale as often as
  # not. They live beside the games on the server instead, and are fetched once.
  keys = {
    into = "config/Ryujinx/system";
    files = [
      "prod.keys"
      # Not every dump needs this one, and a library without it still runs most
      # things, so a missing title.keys is a warning rather than a refusal.
      "title.keys"
    ];
  };

  # Ryujinx keeps its emulated NAND under bis/, which is where save data lands.
  saves = [ "config/Ryujinx/bis/user/save/**" ];
  # The keys are not a save: they are re-fetchable from the server, they are the
  # one thing here worth not copying between machines by accident, and they
  # would otherwise ride along in every bundle.
  saveExcludes = [ "config/Ryujinx/system/*.keys" ];
  legacyPaths = [
    {
      from = "$XDG_CONFIG/Ryujinx/bis";
      into = "config/Ryujinx";
    }
  ];
}
