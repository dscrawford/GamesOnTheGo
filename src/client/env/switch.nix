# Switch — Ryubing, the maintained Ryujinx fork, as nixpkgs dropped the
# original.
#
# Isolated, which is what makes the keys below land somewhere known: Ryujinx
# keeps everything under $XDG_CONFIG_HOME/Ryujinx, and reads its keys from the
# "system" directory inside that. Confirmed by running it against an empty
# config home and reading back the tree it created — Logs, sdcard, system, bis,
# profiles, games.
{ pkgs, helpers, ... }:
{
  emulator = pkgs.ryubing;

  # Switch releases arrive as scene rar sets; the recipe unpacks one into the
  # XCI/NSP the emulator loads, once, on first install.
  recipes = helpers.sceneArchiveRecipe;
  bin = "Ryujinx";
  isolate = true;
  args = [ "{target}" ];

  # Not generated bindings like ares and dolphin get — kept ones. See
  # pads-ryujinx.sh: Ryujinx's settings screen deletes a sleeping pad's entry
  # from Config.json on save, so the client keeps the last working set and puts
  # it back before a launch that would otherwise start unbound.
  padEmulator = "ryujinx";

  preLaunch = ''
    gotg_ryujinx_config="$XDG_CONFIG_HOME/Ryujinx/Config.json"

    # On a first run there is no config to edit yet — Ryujinx writes one on
    # startup — so the settings pinned below would not take until the *second*
    # launch. Starting it once writes the full default config, which is then
    # edited.
    #
    # With the display hidden, so it cannot put a window on screen: Ryujinx
    # takes no flag that means "just write the config and stop" — it treats an
    # unknown argument as a file to load, which is how the first attempt at
    # this opened a second window reporting it "couldn't find any application
    # in '--help'". Blinded it writes the config and exits on its own, seen and
    # discarded here.
    #
    # Seeding a few keys ourselves was the alternative and is worse: a config
    # with no version is rejected outright ("Failed to load config! Loading
    # the default config instead"), and one with a version but few keys leaves
    # the rest at whatever the deserialiser picks rather than at Ryujinx's own
    # defaults.
    if [ ! -f "$gotg_ryujinx_config" ]; then
      env -u DISPLAY -u WAYLAND_DISPLAY ${pkgs.ryubing}/bin/Ryujinx >/dev/null 2>&1 || true
    fi

    # Pinned on every launch, not seeded once: these are what makes a launch go
    # straight to the game and straight back out of it, on a machine where
    # nothing but the game is on screen. The update check is doubly dead weight
    # here — the nix build cannot update itself, only the flake can.
    if [ -f "$gotg_ryujinx_config" ]; then
      if ${pkgs.jq}/bin/jq --argjson fs "$([ -n "$gotg_fullscreen" ] && echo true || echo false)" \
        '.update_checker_type = "Off" | .show_confirm_exit = false | .start_fullscreen = $fs' \
        "$gotg_ryujinx_config" >"$gotg_ryujinx_config.gotg"; then
        mv "$gotg_ryujinx_config.gotg" "$gotg_ryujinx_config"
      else
        rm -f "$gotg_ryujinx_config.gotg"
      fi
    fi
  '';

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
    #
    # This is necessary and not sufficient. A Steam Controller also needs Steam
    # itself to be running: without it the puck stays in lizard mode, emulating
    # a keyboard and mouse, and *no* SDL sees a gamepad — measured with Steam
    # stopped, where even SDL3 reports only the other pad. Both conditions
    # together, or no controller.
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

  # Without firmware Ryujinx stops every game launch on an install dialog, and
  # no setting suppresses it — pre-installing is the only way past. The layout
  # the client writes is exactly what the emulator's own installer produces, so
  # the two are interchangeable. Like the keys it is a console's own software:
  # fetched from the server, never in the store.
  firmware = {
    into = "config/Ryujinx/bis/system/Contents/registered";
    file = "firmware.zip";
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
