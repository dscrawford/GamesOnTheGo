# melonDS, for the Nintendo DS. Every key below was read off melonDS 1.1's own
# Config.cpp / EmuInstance.cpp and a melonDS.toml it wrote, not guessed.
{ pkgs, lib }:
let
  python = pkgs.python3.withPackages (p: [ p.tomlkit ]);
  setConfig = "${python}/bin/python3 ${./melonds-config.py}";
in
{
  melondsPlatform =
    { }:
    {
      emulator = pkgs.melonds;
      bin = "melonDS";
      # The bindings written into melonDS.toml must not touch the player's own install.
      isolate = true;
      args = [
        "{fullscreen}"
        "{target}"
      ];

      # melonDS saves beside the ROM unless told otherwise, which is the
      # library's directory, shared by every machine's copy of it.
      saves = [ "saves/**" ];

      # The Qt menu's fixed shortcuts (Window.cpp): Shift+F<n> saves, F<n> loads.
      # States are named <rom stem>.ml<n> (getSavestateName).
      saveStates = {
        files = "states/{game}.ml{slot}";
        slots = 8;
        save = [
          "LEFTSHIFT"
          "F{slot}"
        ];
        load = [ "F{slot}" ];
      };

      # A real BIOS and firmware, when someone has put them on the server; the
      # built-in FreeBIOS otherwise, which boots nearly everything.
      keys = {
        into = "bios";
        files = [
          "bios7.bin"
          "bios9.bin"
          "firmware.bin"
        ];
        optional = true;
      };

      # pads-melonds.sh leaves melonds-pad.json; the preLaunch merges it.
      padEmulator = "melonds";

      preLaunch = ''
        gotg_melonds_config="$XDG_CONFIG_HOME/melonDS/melonDS.toml"
        mkdir -p "$state/saves" "$state/states"

        gotg_melonds_bios=false
        if [ -s "$state/bios/bios7.bin" ] && [ -s "$state/bios/bios9.bin" ] && [ -s "$state/bios/firmware.bin" ]; then
          gotg_melonds_bios=true
        fi

        # Side by side fills a television; a Deck on its own panel is held like
        # the console, top above bottom. GOTG_NDS_LAYOUT overrides either.
        # Values are Frontend::ScreenLayout: 0 natural, 1 vertical, 2 horizontal, 3 hybrid.
        gotg_melonds_layout=2
        if [ "''${GOTG_MACHINE:-}" = deck ] && [ "''${GOTG_EXTERNAL_DISPLAY:-0}" != 1 ]; then
          gotg_melonds_layout=0
        fi
        case "''${GOTG_NDS_LAYOUT:-}" in
          natural) gotg_melonds_layout=0 ;;
          vertical) gotg_melonds_layout=1 ;;
          horizontal) gotg_melonds_layout=2 ;;
          hybrid) gotg_melonds_layout=3 ;;
        esac

        ${setConfig} "$gotg_melonds_config" "$(${pkgs.jq}/bin/jq -nc \
          --arg saves "$state/saves" --arg states "$state/states" --arg bios "$state/bios" \
          --argjson ext "$gotg_melonds_bios" --argjson layout "$gotg_melonds_layout" '{
            "Instance0.SaveFilePath": $saves,
            "Instance0.SavestatePath": $states,
            "Emu.ExternalBIOSEnable": $ext,
            "DS.BIOS7Path": "\($bios)/bios7.bin",
            "DS.BIOS9Path": "\($bios)/bios9.bin",
            "DS.FirmwarePath": "\($bios)/firmware.bin",
            "Emu.DirectBoot": true,
            "Instance0.Window0.ScreenLayout": $layout,
            "Instance0.Window0.ScreenSizing": 0,
            "Mouse.Hide": true,
            "PauseLostFocus": false
          }')" "@$state/melonds-pad.json" || echo "gotg: could not write melonDS settings" >&2
      '';
    };
}
