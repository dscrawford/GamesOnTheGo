# Paper Mario — PaperBoat, Harbour Masters' native port on the Paper Mario DX
# decompilation. `gotg play usa.paper_mario paperboat`. It replaced ReCut, the
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
#   * **The first launch asks twice.** It extracts pm64.o2r with its own
#     wizard: "No O2R files found. Generate one now?" and then "ROMs found
#     ... Generate the game files from them?" -- Yes to both, then it plays.
#     A ROM on the command line does not skip either (read from
#     src/port/Engine.cpp), so the ROM is staged where the wizard scans, the
#     same way harkinianPort does it, and no launch after that asks again.
{
  pkgs,
  gotgPkgs,
  lib,
  ...
}:
let
  port = gotgPkgs.paperboat;
  # The archive the wizard makes. Matched against the port's own list
  # (sRomArchives); only the US cartridge is supported, so only one name.
  archive = "pm64.o2r";
in
{
  emulator = port;
  bin = "Paperboat";
  # Its settings are inside the game, behind Esc; there is no launcher.
  configurable = false;
  isolate = true;
  args = [ ];
  path = [ pkgs.unzip ];

  preLaunch = ''
    export SHIP_HOME="$state/boat"
    mkdir -p "$SHIP_HOME"
    cd "$SHIP_HOME"

    # An archive belongs to the port version that made it -- 2Ship taught
    # that one (see harkinianPort): an outdated archive stops on a modal no
    # pad reaches. Stamped, and re-extracted when the stamp differs.
    paperboat_stamp="$SHIP_HOME/.gotg-archive-version"
    if [ "$(cat "$paperboat_stamp" 2>/dev/null)" != "${lib.getVersion port}" ]; then
      rm -f "$SHIP_HOME/${archive}"
    fi

    if [ ! -e "$SHIP_HOME/${archive}" ]; then
      echo "first run: staging the ROM for PaperBoat's extractor -- answer Yes twice" >&2
      rm -rf "$state/.rom"
      mkdir -p "$state/.rom"
      unzip -q -o "$install" -d "$state/.rom"
      paperboat_rom="$(find "$state/.rom" -name '*.z64' | head -1)"
      [ -n "$paperboat_rom" ] || { echo "no .z64 inside $install" >&2; exit 1; }
      # A copy: the wizard scans for ROMs by reading files, and the staged
      # tree is removed straight after.
      cp -f "$paperboat_rom" "$SHIP_HOME/gotg-extract.z64"
      rm -rf "$state/.rom"
      printf '%s' "${lib.getVersion port}" >"$paperboat_stamp"
    else
      # Only needed until the archive exists.
      rm -f "$SHIP_HOME/gotg-extract.z64"
    fi
  '';

  # JSON saves, one per slot plus globals, and the settings beside them.
  # The archive is rebuilt from the ROM by the first launch anywhere, and the
  # staged ROM is refetchable, so neither travels.
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
