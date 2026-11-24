# Nintendo 3DS — Azahar, the maintained successor to Citra. Isolated: its
# config is $XDG_CONFIG_HOME/azahar-emu, its NAND, SD card and keys $XDG_DATA_HOME/azahar-emu.
{
  pkgs,
  helpers,
  ...
}:
let
  inherit (helpers) steps;

  python = pkgs.python3;
  cia = ./3ds/cia.py;

  # Where Azahar installs titles: its SD card, under its one console identity.
  sdmc = ''$XDG_DATA_HOME/azahar-emu/sdmc/Nintendo 3DS/00000000000000000000000000000000/00000000000000000000000000000000'';
in
{
  emulator = pkgs.azahar;
  bin = "azahar";
  isolate = true;
  fullscreenFlag = "-f";
  args = [
    "{fullscreen}"
    "{target}"
  ];

  # The game and its update and DLC CIAs as one bundle; the launch installs the CIAs.
  recipes = {
    single_archive = [
      steps.extract7z
      steps.pickLargest
      (steps.collectExtrasOf [ "*.cia" ])
      steps.placeBundle
    ];
  }
  // helpers.bundleRecipe [ "*.cia" ];

  # For encrypted dumps only; decrypted ones run without them.
  keys = {
    into = "data/azahar-emu/sysdata";
    files = [ "aes_keys.txt" ];
  };

  padEmulator = "azahar";

  # Each title's data/, not the installed content beside it. `?` for the
  # space: the client expands these unquoted.
  saves = [
    "data/azahar-emu/sdmc/Nintendo?3DS/*/*/title/*/*/data/**"
    "data/azahar-emu/sdmc/Nintendo?3DS/*/*/extdata/**"
  ];

  # Only the quick-save slot (0) has a hotkey; numbered slots are menu-only.
  saveStates = {
    files = "data/azahar-emu/states/*.00.cst";
    slots = 1;
    save = [
      "LEFTSHIFT"
      "F1"
    ];
    load = [ "F1" ];
  };

  path = [
    pkgs.gawk
    pkgs.coreutils
    pkgs.findutils
    pkgs.gnugrep
  ];

  preLaunch = ''
    gotg_azahar_ini="$XDG_CONFIG_HOME/azahar-emu/qt-config.ini"
    mkdir -p "''${gotg_azahar_ini%/*}"

    # Azahar ignores a value whose `\default` marker is true, so the marker is
    # cleared too. ENVIRON, as awk -v would eat Qt's backslashes.
    gotg_azahar_set() {
      touch "$gotg_azahar_ini"
      GOTG_S="[$1]" GOTG_K="$2" GOTG_V="$3" awk '
        function emit() { print ENVIRON["GOTG_K"] "=" ENVIRON["GOTG_V"]; print ENVIRON["GOTG_K"] "\\default=false"; done = 1 }
        /^\[/ { if (in_s && !done) emit(); in_s = ($0 == ENVIRON["GOTG_S"]) }
        in_s && (index($0, ENVIRON["GOTG_K"] "=") == 1 || index($0, ENVIRON["GOTG_K"] "\\default=") == 1) { if (!done) emit(); next }
        { print }
        END { if (!done) { if (!in_s) print ENVIRON["GOTG_S"]; emit() } }
      ' "$gotg_azahar_ini" >"$gotg_azahar_ini.tmp" && mv "$gotg_azahar_ini.tmp" "$gotg_azahar_ini"
    }

    # The large top screen with the touch screen small beside it.
    gotg_azahar_set Layout layout_option 2
    # Nobody is at a keyboard to answer these.
    gotg_azahar_set UI confirmClose false
    gotg_azahar_set UI saveStateWarning false
    gotg_azahar_set UI enable_discord_presence false
    gotg_azahar_set UI hideInactiveMouse true
    # The overlay's save-state row presses these (saveStates above).
    gotg_azahar_set UI 'Shortcuts\Main%20Window\Quick%20Save\KeySeq' Shift+F1
    gotg_azahar_set UI 'Shortcuts\Main%20Window\Quick%20Load\KeySeq' F1

    # Each CIA once, keyed by name and size so a re-release installs again.
    gotg_azahar_installed="$state/installed-cias"
    touch "$gotg_azahar_installed"
    gotg_azahar_install() {
      local key
      key="''${install##*/}/''${1##*/} $(stat -c %s "$1")"
      grep -qxF -- "$key" "$gotg_azahar_installed" && return 0
      echo "gotg-play: installing ''${1##*/} into Azahar" >&2
      if env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen \
        ${pkgs.azahar}/bin/azahar -i "$1" >&2; then
        printf '%s\n' "$key" >>"$gotg_azahar_installed"
      else
        echo "gotg-play: Azahar could not install ''${1##*/}" >&2
      fi
    }
    case "$target" in
      *.cia | *.CIA) gotg_azahar_install "$target" ;;
    esac
    if [ -d "$install/extras" ]; then
      while IFS= read -r -d "" gotg_extra_cia; do
        gotg_azahar_install "$gotg_extra_cia"
      done < <(find "$install/extras" -maxdepth 1 -type f -iname '*.cia' -print0 | sort -z)
    fi

    # An installed CIA boots from the .app its TMD names.
    case "$target" in
      *.cia | *.CIA)
        if gotg_azahar_ids="$(${python}/bin/python3 -I ${cia} "$target")"; then
          gotg_azahar_tid="''${gotg_azahar_ids% *}"
          gotg_azahar_app="${sdmc}/title/''${gotg_azahar_tid:0:8}/''${gotg_azahar_tid:8:8}/content/''${gotg_azahar_ids#* }.app"
          if [ -f "$gotg_azahar_app" ]; then
            target="$gotg_azahar_app"
          else
            echo "gotg-play: $gotg_azahar_app is not installed; Azahar will offer to install the CIA" >&2
          fi
        fi
        ;;
    esac
  '';
}
