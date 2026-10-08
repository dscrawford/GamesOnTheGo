# Paper Mario — PaperBoat, Harbour Masters' native port on the Paper Mario DX
# decompilation. `nix run gotg#usa.paper_mario.paperboat`. It replaced ReCut, the
# Windows-only static recompilation this variant used to run under Wine: this
# one ships for Linux, renders through libultraship (resolution scaling,
# interpolated frame rates, aspect ratios, a mod menu), and is the same
# family as the Ocarina of Time, Majora's Mask and Smash ports beside it.
#
# Three things about it shape this file:
#
#   * **Everything it writes goes to $SHIP_HOME.** It is a portable build,
#     so without that it writes into its working directory -- the archive,
#     the saves, the settings. Pointed into the environment's state, one
#     directory holds all of it, and the read-only files it ships are found
#     beside the binary in the store (see pkgs/paperboat).
#
#   * **The ROM is unpacked here, not by a recipe.** The Harkinian ports get
#     a bare .z64 from data/overrides.json's `unzip`, but that is keyed by
#     game, and this game's plain entry is ares, which reads the No-Intro zip
#     as it is. So the zip stays a zip and the first launch takes the .z64
#     out of it, as ReCut did.
#
#   * **The archive is made by a command, not by its wizard.** PaperBoat
#     extracts pm64.o2r behind two popups ("No O2R files found. Generate one
#     now?", "ROMs found ...") that a ROM on the command line does not skip
#     (src/port/Engine.cpp), and every machine asked again. Torch, the
#     extractor PaperBoat links in, is packaged on its own (pkgs/paperboat-
#     torch) and run here as BattleShip runs it: same commit, same options,
#     the same config.yml and asset yamls from PaperBoat's own package.
#     Checked against an archive the wizard made from the same ROM: all
#     60,824 entries alike, version and portVersion included; the wizard's
#     has one more, audio/portVersion, a copy of the stamp nothing reads.
#     About thirty seconds, once per machine and PaperBoat version.
{
  pkgs,
  gotgPkgs,
  lib,
  helpers,
  ...
}:
let
  port = gotgPkgs.paperboat;
  torch = gotgPkgs.paperboat-torch;
  # The archive config.yml names. Matched against the port's own list
  # (sRomArchives); only the US cartridge is supported, so only one name.
  archive = "pm64.o2r";
in
{
  emulator = port;
  bin = "Paperboat";
  # A port, not the platform emulator: `gotg play <id> emulate` is the way
  # back to ares when this one misbehaves, as it is for DK64 and the
  # Harkinian ports.
  nativePort = true;
  # A wired Xbox 360 pad, to this port, as ReCut had and this replacement
  # lost. libultraship reads pads through SDL2 and the gamecontrollerdb.txt
  # it ships, and a clone mirroring the Deck's own controls (28de:1205, a
  # hidraw pad with no SDL mapping as an evdev device) was in no database:
  # on the Deck nothing moved, the left stick included, while every pad SDL
  # knew by heart worked at a desk. 045e:028e is the one every SDL maps.
  # The identity also makes the clone the same GUID on every machine, so the
  # port mapping in paperboat.cfg.json, which travels with the saves, fits
  # wherever it lands -- and it puts the overlay on the generic walk, whose
  # right stick and left trigger are the C buttons and Z this port reads;
  # the N64 walk lit neither. See docs/requests/look-like-an-xbox-pad.md.
  padIdentity = "xbox360";
  # Its settings are inside the game, behind Esc; there is no launcher.
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
    cd "$SHIP_HOME"

    # Select opens the port's own menu: see menuFromPad. PaperBoat's
    # cmake/lus-cvars.cmake prefixes libultraship's name, as the Zelda ports
    # do -- gSettings.ControlNav. (A launch wrote the unprefixed gControlNav
    # for a day; the game kept the key and ignored it.)
    ${helpers.menuFromPad {
      file = ''"$SHIP_HOME/paperboat.cfg.json"'';
      cvar = "gSettings.ControlNav";
    }}

    # An archive belongs to the port version that made it -- 2Ship taught
    # that one (see harkinianPort): an outdated archive stops on a modal no
    # pad reaches. Stamped, and re-extracted when the stamp differs.
    paperboat_stamp="$SHIP_HOME/.gotg-archive-version"
    if [ "$(cat "$paperboat_stamp" 2>/dev/null)" != "${lib.getVersion port}" ]; then
      rm -f "$SHIP_HOME/${archive}"
    fi

    if [ ! -e "$SHIP_HOME/${archive}" ]; then
      echo "first run: extracting game assets from $install -- half a minute, once" >&2
      paperboat_work="$state/.extract"
      rm -rf "$paperboat_work"
      mkdir -p "$paperboat_work/rom" "$paperboat_work/out"
      unzip -q -o "$install" -d "$paperboat_work/rom"
      paperboat_rom="$(find "$paperboat_work/rom" -name '*.z64' | head -1)"
      [ -n "$paperboat_rom" ] || { echo "no .z64 inside $install" >&2; exit 1; }
      # -s is where config.yml and the asset yamls are: PaperBoat's own, so
      # the recipe is the one this port reads. -u writes portVersion, as
      # PaperBoat's extractor does. Into a scratch directory and then moved,
      # so an extraction that is stopped leaves no half an archive behind.
      (cd "$paperboat_work" && ${torch}/bin/torch o2r \
        -s "${port}/share/paperboat" -d "$paperboat_work/out" \
        -u "${lib.getVersion port}" "$paperboat_rom" >"$paperboat_work/torch.log" 2>&1) || {
        tail -20 "$paperboat_work/torch.log" >&2
        echo "could not extract PaperBoat's assets from $install" >&2
        exit 1
      }
      mv -f "$paperboat_work/out/${archive}" "$SHIP_HOME/${archive}"
      printf '%s' "${lib.getVersion port}" >"$paperboat_stamp"
      rm -rf "$paperboat_work"
    fi
  '';

  # JSON saves, one per slot plus globals, and the settings beside them.
  # The archive is made from the ROM by the first launch anywhere, so it
  # does not travel.
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
