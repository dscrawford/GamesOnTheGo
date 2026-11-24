# PlayStation 2 — PCSX2, which reads the CHD the recipe makes from a Redump .7z.
{ pkgs, helpers, ... }:
let
  # The console BIOS, placed by hand in the server's ps2 directory beside the
  # games and fetched once, like the Switch's keys.
  bios = "ps2-bios.bin";
in
{
  emulator = pkgs.pcsx2;
  bin = "pcsx2-qt";

  # PCSX2 keeps everything under $XDG_CONFIG_HOME/PCSX2; isolated, that is
  # {state}/config/PCSX2, and every setting below is this environment's own.
  isolate = true;
  fullscreenFlag = "-fullscreen";

  # -batch exits with the game rather than falling back to a library window,
  # and -nogui never opens that window: neither is answerable from a sofa.
  args = [
    "-batch"
    "-nogui"
    "{fullscreen}"
    "--"
    "{target}"
  ];

  keys = {
    into = "config/PCSX2/bios";
    files = [ bios ];
  };

  padEmulator = "pcsx2";
  recipes = helpers.chdArchiveRecipe;

  saves = [ "config/PCSX2/memcards/**" ];
  # Bound in [Hotkeys] below: Shift+F<n> saves slot n, F<n> loads it.
  saveStates = {
    files = "config/PCSX2/sstates/*.{slot2}.p2s";
    slots = 8;
    save = [
      "LEFTSHIFT"
      "F{slot}"
    ];
    load = [ "F{slot}" ];
  };

  preLaunch = ''
    gotg_pcsx2_ini="$XDG_CONFIG_HOME/PCSX2/inis/PCSX2.ini"
    mkdir -p "$(dirname "$gotg_pcsx2_ini")"
    touch "$gotg_pcsx2_ini"

    # One key of one section, added with its section when PCSX2 has not
    # written either yet.
    gotg_pcsx2_set() {
      ${pkgs.gawk}/bin/awk -v section="[$1]" -v key="$2" -v value="$3" '
        BEGIN { line = key " = " value }
        /^\[/ {
          if (inSection && !written) { print line; written = 1 }
          inSection = ($0 == section)
        }
        inSection && index($0, key " ") == 1 { print line; written = 1; next }
        { print }
        END { if (!written) { if (!inSection) print section; print line } }
      ' "$gotg_pcsx2_ini" >"$gotg_pcsx2_ini.gotg" && mv "$gotg_pcsx2_ini.gotg" "$gotg_pcsx2_ini"
    }

    # Without the version PCSX2 takes the file for a stale one and resets it
    # to defaults, dropping everything written here.
    gotg_pcsx2_set UI SettingsVersion 1
    # The first-run wizard, the "are you sure" on stop and a pause whenever the
    # overlay takes focus: each one a game nobody can reach with a pad.
    gotg_pcsx2_set UI SetupWizardIncomplete false
    gotg_pcsx2_set UI ConfirmShutdown false
    gotg_pcsx2_set UI PauseOnFocusLoss false
    gotg_pcsx2_set UI HideMouseCursor true
    gotg_pcsx2_set AutoUpdater CheckAtStartup false
    gotg_pcsx2_set Filenames BIOS ${bios}
    gotg_pcsx2_set InputSources SDL true

    # The whole section, so a default from an older run (F1 saves the selected
    # slot, F3 loads it) cannot fire beside the slot keys the overlay presses.
    ${pkgs.gawk}/bin/awk '
      /^\[/ { drop = ($0 == "[Hotkeys]") }
      !drop { print }
    ' "$gotg_pcsx2_ini" >"$gotg_pcsx2_ini.gotg" && mv "$gotg_pcsx2_ini.gotg" "$gotg_pcsx2_ini"
    {
      echo "[Hotkeys]"
      echo "OpenPauseMenu = Keyboard/Escape"
      echo "ToggleFullscreen = Keyboard/Alt & Keyboard/Return"
      for gotg_slot in 1 2 3 4 5 6 7 8; do
        echo "SaveStateToSlot$gotg_slot = Keyboard/Shift & Keyboard/F$gotg_slot"
        echo "LoadStateFromSlot$gotg_slot = Keyboard/F$gotg_slot"
      done
    } >>"$gotg_pcsx2_ini"
  '';
}
