# Paper Mario on PaperBoat, the native port. The zip is unpacked here rather
# than by a recipe (the plain entry is ares, which reads the zip), and the
# archive is made by Torch, not the port's wizard, whose popups block a launch.
{
  pkgs,
  gotgPkgs,
  lib,
  helpers,
  ...
}:
let
  inherit (import ../../emulators/jq-edit.nix { inherit pkgs; }) gotgJqEdit;
  port = gotgPkgs.paperboat;
  torch = gotgPkgs.paperboat-torch;
  # The name the port's config.yml gives the US cartridge's archive.
  archive = "pm64.o2r";
in
{
  emulator = port;
  bin = "Paperboat";
  nativePort = true;
  # SDL2's gamecontrollerdb knows no Deck clone (28de:1205): nothing moved on
  # a Deck until the clones looked like the one pad every SDL maps.
  padIdentity = "xbox360";
  configurable = false;
  isolate = true;
  args = [ ];
  path = [
    pkgs.unzip
    pkgs.jq
  ];

  preLaunch = ''
    export SHIP_HOME="$state/boat"
    mkdir -p "$SHIP_HOME"

    # The HD texture pack (a mod extra in the bundle) is linked in under the
    # one name the port loads. A regular file left by the old fetch stays; a
    # link whose bundle is gone is removed before the port trips on it.
    paperboat_hd="$(gotg_extra '*.o2r')"
    paperboat_hd_new=""
    if [ -n "$paperboat_hd" ]; then
      if [ "$(readlink "$SHIP_HOME/paperboat-hd.o2r" 2>/dev/null)" != "$paperboat_hd" ]; then
        ln -sfn "$paperboat_hd" "$SHIP_HOME/paperboat-hd.o2r"
        paperboat_hd_new=1
      fi
    elif [ -L "$SHIP_HOME/paperboat-hd.o2r" ] && [ ! -e "$SHIP_HOME/paperboat-hd.o2r" ]; then
      rm -f "$SHIP_HOME/paperboat-hd.o2r"
    fi

    cd "$SHIP_HOME"

    # Cvar names from PaperBoat's cmake/lus-cvars.cmake: it prefixes
    # libultraship's (an unprefixed gControlNav was silently ignored).
    ${helpers.lusSettings {
      file = ''"$SHIP_HOME/paperboat.cfg.json"'';
      unsaid = {
        "gSettings.ControlNav" = 1;
        "gSettings.SdlWindowedFullscreen" = 1;
        "gSettings.MatchRefreshRate" = 1;
        "gSettings.MSAAValue" = 4;
        # The pack's textures are the archive's alt/ set, drawn only with this on.
        "gEnhancements.Mods.AlternateAssets" = 1;
      };
    }}
    # A pack that just arrived (or changed) turns alt assets on even where a
    # player had switched them off; the in-game toggle still holds per session.
    if [ -n "$paperboat_hd_new" ]; then
      ${gotgJqEdit {
        file = "$lus_cfg";
        filter = ".CVars.gEnhancements.Mods.AlternateAssets = 1";
        jq = "jq";
        force = true;
        suffix = ".gotg-tmp";
        indent = "      ";
      }}
    fi

    # An archive from another port version stops on a modal no pad reaches.
    paperboat_stamp="$SHIP_HOME/.gotg-archive-version"
    if [ "$(cat "$paperboat_stamp" 2>/dev/null)" != "${lib.getVersion port}" ]; then
      rm -f "$SHIP_HOME/${archive}"
    fi

    if [ ! -e "$SHIP_HOME/${archive}" ]; then
      # $target is the zip whether or not the game is a bundle.
      echo "first run: extracting game assets from $target -- half a minute, once" >&2
      paperboat_work="$state/.extract"
      rm -rf "$paperboat_work"
      mkdir -p "$paperboat_work/rom" "$paperboat_work/out"
      unzip -q -o "$target" -d "$paperboat_work/rom"
      paperboat_rom="$(find "$paperboat_work/rom" -name '*.z64' | head -1)"
      [ -n "$paperboat_rom" ] || { echo "no .z64 inside $target" >&2; exit 1; }
      # Torch with PaperBoat's own yamls and version stamp, into scratch so an
      # interrupted extraction leaves no half-archive.
      (cd "$paperboat_work" && ${torch}/bin/torch o2r \
        -s "${port}/share/paperboat" -d "$paperboat_work/out" \
        -u "${lib.getVersion port}" "$paperboat_rom" >"$paperboat_work/torch.log" 2>&1) || {
        tail -20 "$paperboat_work/torch.log" >&2
        echo "could not extract PaperBoat's assets from $target" >&2
        exit 1
      }
      mv -f "$paperboat_work/out/${archive}" "$SHIP_HOME/${archive}"
      printf '%s' "${lib.getVersion port}" >"$paperboat_stamp"
      rm -rf "$paperboat_work"
    fi
  '';

  # The archive is remade from the ROM anywhere, so it does not travel.
  saves = [
    "boat/saves/**"
    "boat/paperboat.cfg.json"
  ];
  saveExcludes = [
    "boat/*.o2r"
    "boat/*.z64"
    "boat/logs/**"
    "boat/mods/**"
    "boat/imgui.ini"
  ];
}
