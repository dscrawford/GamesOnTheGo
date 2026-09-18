# Snowboard Kids 2 — the N64Recomp/RT64 static recompilation, in place of
# ares. The same family as Donkey Kong 64 beside it, and packaged the same
# way: upstream ships a native Linux build, so there is no Wine prefix, just
# the binary patched onto nixpkgs' libraries in pkgs/snowboardkids2recomp.
#
# What the port buys, measured rather than assumed — a first run writes
# graphics.json with "rr_option": "Display", so it draws at the monitor's
# refresh rate instead of the N64's cap, and the runtime decouples framerate
# from game speed, so nothing about the racing changes. "rr_option":
# "Manual" with rr_manual_value is the way back to a fixed number.
#
# The sequel, not the original: Snowboard Kids 1 is fully decompiled
# (tenry92/sbk-decomp) but nobody has built a port on that yet, so
# usa.snowboard_kids stays on ares at 30fps.
#
# Two things about this port shape the file, both inherited from the shared
# N64ModernRuntime and both checked against this binary:
#
#   * **It ignores XDG_CONFIG_HOME.** Run with XDG_CONFIG_HOME and HOME
#     pointed at different directories and the settings land in
#     "$HOME/.config/SnowboardKids2Recompiled" regardless. `isolate` only
#     exports the XDG variables, so redirecting HOME is what actually keeps
#     this out of the player's real home.
#
#   * **The ROM goes through the launcher's picker, once.** The binary
#     carries check_all_stored_roms/load_stored_rom, the same pair DK64 has:
#     it keeps its own copy after you choose one. So the recipe leaves a bare
#     .z64 in the games directory and the first launch is one "Load ROM"
#     click at it; no launch after it asks again.
{
  pkgs,
  lib,
  helpers,
  gotgPkgs,
  ...
}:
{
  emulator = gotgPkgs.snowboardkids2recomp;
  bin = "SnowboardKids2Recompiled";
  # Its settings are in-game — the launcher's own Settings entry — so there
  # is no separate configuration screen to open, and `gotg configure`
  # opening it would just start the game.
  configurable = false;
  isolate = true;
  # The ROM is not a command-line argument here; the launcher holds it.
  args = [ ];

  # The port reads a bare .z64, not the No-Intro zip it travels as. Both
  # handlers, because one zip is single_file from the games-root pass and
  # no_intro_set from the DAT path. The matching half is data/overrides.json
  # marking this entry `unzip`, which is what makes {target} the .z64 inside.
  recipes =
    let
      unpacked = [
        helpers.steps.unzip
        helpers.steps.placeTree
      ];
    in
    {
      single_file = unpacked;
      no_intro_set = unpacked;
    };

  env = {
    # The one that does the isolating. See the note at the top.
    HOME = "{state}";
  };

  path = [ pkgs.unzip ];

  preLaunch = ''
    export HOME="$state"
    mkdir -p "$state/.config/SnowboardKids2Recompiled/mods"

    # $target is the bare .z64 the recipe left in the games directory, so
    # there is nothing to unpack here — only the one thing this port cannot
    # be told from the outside.
    if [ -z "$(find "$state/.config/SnowboardKids2Recompiled" -maxdepth 1 -name '*.z64' -print -quit 2>/dev/null)" ]; then
      echo "first run: choose Load ROM and pick $target — asked once, then stored" >&2
    fi
  '';

  # Saves and the settings beside them are small and worth keeping in step
  # across machines. mod_config travels too: which mods are switched on is a
  # choice, not a derived file.
  saves = [
    ".config/SnowboardKids2Recompiled/saves/**"
    ".config/SnowboardKids2Recompiled/*.json"
    ".config/SnowboardKids2Recompiled/mod_config/**"
  ];
  saveExcludes = [
    ".config/SnowboardKids2Recompiled/mods/**"
    # The ROM copy the launcher stored. Nothing in `saves` matches a .z64
    # today, so this guards a widening of that glob rather than a live path —
    # a copyrighted ROM must never start syncing between machines.
    ".config/SnowboardKids2Recompiled/*.z64"
  ];
}
