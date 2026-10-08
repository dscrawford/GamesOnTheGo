# The HarbourMasters native ports. Split out of helpers.nix: what each of these
# needs to be driven correctly is its own body of knowledge, and they were only
# ever neighbours in one file.
{
  lib,
  pkgs,
  steps,
}:
let
  # A libultraship port's settings, as a launch leaves them.
  #
  # `unsaid` is cvar -> value, each written only when the file does not say
  # either way: a first launch anywhere has them, and a person who changed
  # one in the port's menu stays heard. The settings file travels with each
  # port's saves. What goes in it, and why:
  #
  #   - The menu from a pad. libultraship opens its menu on Esc or F1 and --
  #     only while the controller navigation cvar is set -- on the pad's Back
  #     (Gui.cpp, TOGGLE_PAD_BTN; its ImGui patch is what lets Back be read
  #     while the menu is closed). It ships off, and the checkbox that turns
  #     it on is inside the menu a pad cannot open: on the Deck, Select did
  #     nothing in PaperBoat and there was no keyboard to press Esc on.
  #   - Full screen at the screen's resolution. Full screen in libultraship is
  #     a mode switch to Window.Fullscreen.Width x Height, 1280x720 unless
  #     somebody changed it: 720p stretched over a 4K television, letterboxed
  #     on a Deck's 800 lines. "SDL windowed fullscreen" is a borderless
  #     window the size of the desktop instead -- the screen's own
  #     resolution, whatever is plugged in, and no mode switch.
  #   - The frame rate the screen has. The ports interpolate to a chosen rate
  #     (20 or 30 unless changed); "match refresh rate" follows the display,
  #     165 on a desk, 90 on a Deck OLED, 60 on a television.
  #   - 4x MSAA. Cheap on N64 geometry at any resolution these run at.
  #
  # Every name is read off the port's own cmake/lus-cvars.cmake (or, for a
  # port without one, libultraship's Compat.h fallbacks), not inferred from
  # the config file -- that guess cost a day with PaperBoat's menu. A dot is a
  # level of JSON, as libultraship's Config stores it. `file` is a shell
  # expression for the settings file.
  #
  # And, each launch rather than when unsaid: Window.Fullscreen.Enabled from
  # how the game was started (lib.nix's gotg_fullscreen -- Steam and the
  # picker full screen, a terminal not), since that is how every other
  # environment here decides it and the ports have no flag for it.
  lusSettings =
    { file, unsaid }:
    let
      path = cvar: ''["CVars"] + ("${cvar}" | split("."))'';
      value = v: if builtins.isBool v then (if v then "1" else "0") else toString v;
      program = lib.concatStringsSep " | " (
        lib.mapAttrsToList (
          cvar: v: "(if getpath(${path cvar}) == null then setpath(${path cvar}; ${value v}) else . end)"
        ) unsaid
        ++ [ ".Window.Fullscreen.Enabled = $fs" ]
      );
    in
    ''
      lus_cfg=${file}
      lus_fs="$([ -n "''${gotg_fullscreen:-}" ] && echo true || echo false)"
      if [ ! -e "$lus_cfg" ]; then
        jq -n --argjson fs "$lus_fs" '{} | ${program}' >"$lus_cfg"
      else
        jq --argjson fs "$lus_fs" '${program}' "$lus_cfg" >"$lus_cfg.gotg-tmp" \
          && mv -f "$lus_cfg.gotg-tmp" "$lus_cfg"
      fi
    '';
in
{
  inherit lusSettings;

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
  #   config    the settings file libultraship keeps beside the archive
  #             (shipofharkinian.json, 2ship2harkinian.json); carried with the
  #             saves, and where `lusSettings` writes
  #   settings  the port's names for libultraship's cvars, read off its
  #             cmake/lus-cvars.cmake: `prefix` is CVAR_PREFIX_SETTING with
  #             its dot ("gSettings."; BattleShip has none and "g"), and
  #             `refresh` the match-refresh-rate cvar, which each port names
  #             for itself (2Ship: gMatchRefreshRate) or lacks (null)
  harkinianPort =
    {
      port,
      bin,
      appName,
      archives,
      config,
      settings ? { },
    }:
    let
      prefix = settings.prefix or "gSettings.";
      refresh = settings.refresh or "gSettings.MatchRefreshRate";
      unsaid = {
        "${prefix}ControlNav" = 1;
        "${prefix}SdlWindowedFullscreen" = 1;
        "${prefix}MSAAValue" = 4;
      }
      // lib.optionalAttrs (refresh != null) { "${refresh}" = 1; };
    in
    {
      emulator = port;
      inherit bin;
      # jq, for the settings file `lusSettings` edits.
      path = [ pkgs.jq ];
      # Ports, all of them -- that is what this helper is. `emulate` is the
      # way back to ares.
      nativePort = true;
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
        "data/${appName}/${config}"
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

      # An archive belongs to the port version that made it. 2Ship 5.0 met
      # 4.0.2's mm.o2r with a modal "Outdated ROM Archive ... You will now be
      # redirected to re-extract them" and an OK button that no pad reaches
      # -- the game never started. So the archive is stamped with the version
      # that extracted it, and one stamped otherwise, or not at all (made
      # before the stamp existed), is removed and extracted again. A minor
      # bump that did not need it costs a minute of extraction once.
      preLaunch = ''
        harkinian_data="''${XDG_DATA_HOME:-$HOME/.local/share}/${appName}"
        mkdir -p "$harkinian_data"
        ${lusSettings {
          file = ''"$harkinian_data/${config}"'';
          inherit unsaid;
        }}
        harkinian_stamp="$harkinian_data/.gotg-archive-version"
        if [ "$(cat "$harkinian_stamp" 2>/dev/null)" != "${lib.getVersion port}" ]; then
          rm -f ${lib.concatMapStringsSep " " (a: ''"$harkinian_data/${a}"'') archives}
        fi
        if ${lib.concatMapStringsSep " && " (a: ''[ ! -e "$harkinian_data/${a}" ]'') archives}; then
          echo "first run: extracting game assets from $target" >&2
          mkdir -p "$harkinian_data"
          # Stamped before the exec, since nothing runs after it; an extraction
          # that fails leaves no archive, and the check above tries again.
          printf '%s' "${lib.getVersion port}" >"$harkinian_stamp"
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
