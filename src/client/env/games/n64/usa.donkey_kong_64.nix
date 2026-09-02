# Donkey Kong 64 — Rekongpiled, the N64Recomp/RT64 static recompilation, in
# place of ares. The same family as the Paper Mario ReCut beside it, and the
# easier one: upstream publishes a native Linux x64 build, so there is no Wine
# prefix here — just the binary, patched onto nixpkgs' libraries in
# pkgs/dk64recomp.
#
# What it buys over the emulator: RT64 rendering with the N64's effects intact,
# high framerate decoupled from game speed, low input lag and instant loads.
# The DK64-specific reason to prefer it is the mod runtime — see below.
#
# Two things about this port shape the file:
#
#   * **It ignores XDG_CONFIG_HOME.** Measured, not assumed: run with
#     XDG_CONFIG_HOME and HOME pointed at different directories and the
#     settings land in "$HOME/.config/DK64Recompiled" every time. `isolate`
#     only exports the XDG variables, so on its own it would leave this
#     writing into the player's real home. Redirecting HOME is what actually
#     isolates it, and everything below is relative to that.
#
#   * **The ROM goes through the launcher's picker, once.** Unlike the ReCut,
#     which documents a path to drop the ROM at, this one stores the ROM
#     itself after you choose it ("check_all_stored_roms" in the binary) and
#     the FAQ answers "how do I choose a different ROM?" with "you don't".
#     So the recipe below leaves a bare .z64 in the games directory and the
#     first launch is one "Load ROM" click at it; no launch after it asks
#     again.
#
# The unpacking is the recipe's job, not this file's — see the `recipes`
# attribute. Doing it in preLaunch instead would mean reimplementing the
# extraction limits and the symlink refusal that steps.unzip and
# steps.placeTree already carry, once per environment that needs a bare ROM.
{
  pkgs,
  lib,
  helpers,
  gotgPkgs,
  ...
}:
let
  # The mods are .nrm archives — N64ModernRuntime's own format, which this
  # runtime loads from its mods directory and lists in the in-game Mods menu.
  # Installing them is a file copy; whether each is *on* is the player's
  # choice, made in that menu and remembered in mod_config.
  #
  # Both are DK64-specific quality-of-life rather than content:
  #   tag-anywhere  swap Kong without walking back to a tag barrel
  #   beaver-bother the Beaver Bother minigame, made bearable
  mods = {
    "dk64_tag_anywhere.nrm" = pkgs.fetchurl {
      url =
        "https://github.com/Killklli/DK64TagAnywhereRecomp/releases/download/"
        + "v1.0.1/dk64_tag_anywhere.zip";
      hash = "sha256-x+u2nj7XdA16z4w/DShDaX3uVZqfWX2GRYzbUucHq7U=";
    };
    "fixed_beaver_bother.nrm" = pkgs.fetchurl {
      url =
        "https://github.com/theballaam96/RecompFixedBeaverBother/releases/download/"
        + "1.0.0/fixed_beaver_bother.zip";
      hash = "sha256-TRuXZOa4SSfyNikwdCr6KFNMWpJfENcDr20M3+Vw9u0=";
    };
  };
  installMods = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (name: zip: ''
      if [ ! -f "$mods_dir/${name}" ]; then
        echo "installing mod: ${name}" >&2
        unzip -q -o ${zip} -d "$mods_dir"
      fi
    '') mods
  );
in
{
  emulator = gotgPkgs.dk64recomp;
  bin = "DK64Recompiled";
  # Its settings are in-game — the launcher's own Settings entry — so there is
  # no separate configuration screen to open, and `gotg configure` opening it
  # would just start the game.
  configurable = false;
  isolate = true;
  # The ROM is not a command-line argument here; the launcher holds it.
  args = [ ];

  # The port reads a bare .z64, not the No-Intro zip it travels as — the same
  # need the HarbourMasters ports have, met the same way. Both handlers,
  # because one zip is single_file from the games-root pass and no_intro_set
  # from the DAT torrent path. The matching half is data/overrides.json
  # marking this entry `unzip`, which is what puts the unpacked tree where
  # `gotg list` can find it offline and makes {target} the .z64 inside it.
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
    mods_dir="$state/.config/DK64Recompiled/mods"
    mkdir -p "$mods_dir"

    ${installMods}

    # $target is the bare .z64 the recipe left in the games directory, so
    # there is nothing to unpack here — only the one thing this port cannot
    # be told from the outside.
    if [ ! -f "$state/.config/DK64Recompiled/DK64.z64" ]; then
      echo "first run: choose Load ROM and pick $target — asked once, then stored" >&2
    fi
  '';

  # The FAQ names the save location, and the settings beside it are small and
  # worth keeping in step across machines. mod_config travels too: which mods
  # are switched on is a choice, not a derived file.
  saves = [
    ".config/DK64Recompiled/saves/**"
    ".config/DK64Recompiled/*.json"
    ".config/DK64Recompiled/mod_config/**"
  ];
  # The .nrm files are refetched by the store on any machine, and the ROM copy
  # is the player's own — neither is worth carrying.
  saveExcludes = [
    ".config/DK64Recompiled/mods/**"
    "rom/**"
  ];
}
