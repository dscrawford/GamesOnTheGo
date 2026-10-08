# Donkey Kong 64 — Rekongpiled, the N64Recomp/RT64 static recompilation, in
# place of ares: RT64 rendering, framerate decoupled from game speed, and a mod
# runtime. The same family as Paper Mario ReCut (since replaced by PaperBoat), and
# the easier one — upstream ships a native Linux build, so there is no Wine prefix, just
# the binary patched onto nixpkgs' libraries in pkgs/dk64recomp.
#
# What every N64Recomp port shares -- it ignores XDG_CONFIG_HOME, the ROM is
# stored by the port once, the pad it wants -- is in emulators/recomp-port.nix.
# What is this game's: the mods below, and the name its stored ROM goes by.
#
# The unpacking is the recipe's job, not this file's — see `recipes` in
# recomp-port.nix. Doing it in preLaunch instead would mean reimplementing the
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
  # .nrm archives — N64ModernRuntime's format. Installing is a file copy, and
  # whether each is on is the player's choice, made in the in-game Mods menu
  # and remembered in mod_config.
  #   tag-anywhere   swap Kong without walking back to a tag barrel
  #   beaver-bother  the Beaver Bother minigame, made bearable
  mods = {
    "dk64_tag_anywhere.nrm" = pkgs.fetchurl {
      url =
        "https://github.com/Killklli/DK64TagAnywhereRecomp/releases/download/"
        + "v1.0.3/dk64_tag_anywhere.zip";
      hash = "sha256-w5/Pq0c1FKZIYDePjyPUOPo4KrwOFa+Sl1yI2kWO1jA=";
    };
    "fixed_beaver_bother.nrm" = pkgs.fetchurl {
      url =
        "https://github.com/theballaam96/RecompFixedBeaverBother/releases/download/"
        + "1.0.1/fixed_beaver_bother.zip";
      hash = "sha256-dBZsZpngcRbdt8IJGVYTzBiKFoFUQPEen1n3IGfVOao=";
    };
  };
  # Each mod is reinstalled when the zip it came from changes, not only when
  # it is missing: an "is it there" test kept the first version of every mod
  # in a state directory for good, whatever this file was bumped to. The
  # stamp in mod-stamps/ names the store path it was unpacked from -- kept out
  # of mods/, where the runtime logs every file that is not a mod.
  installMods = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (name: zip: ''
      if [ ! -f "$mods_dir/${name}" ] \
        || [ "$(cat "$stamps_dir/${name}" 2>/dev/null)" != "${zip}" ]; then
        echo "installing mod: ${name}" >&2
        unzip -q -o ${zip} -d "$mods_dir"
        printf '%s' "${zip}" >"$stamps_dir/${name}"
      fi
    '') mods
  );
in
helpers.recompPort {
  emulator = gotgPkgs.dk64recomp;
  bin = "DK64Recompiled";
  dir = "DK64Recompiled";
}
// {
  preLaunch = ''
    export HOME="$state"
    mods_dir="$state/.config/DK64Recompiled/mods"
    stamps_dir="$state/.config/DK64Recompiled/mod-stamps"
    mkdir -p "$mods_dir" "$stamps_dir"

    ${installMods}

    # The ROM, put where the port keeps the copy it makes for itself, so its
    # picker never opens. N64ModernRuntime stores a picked ROM as
    # `<game_id>.z64` beside the settings — GameEntry::stored_filename() is
    # `game_id + u8".z64"` — and check_all_stored_roms() looks there first.
    #
    # `DK64`, capitals and all, taken from upstream's own registration
    # (`.game_id = u8"DK64"`). Not the `dk64` its mods declare: that is the
    # separate mod_game_id, and a file named after it is simply never looked
    # at. See the same note in usa.snowboard_kids_2.nix, where the two ids
    # differ more visibly.
    #
    # A copy rather than a link: this is the port's file to manage — it
    # deletes it outright when the hash does not match — and it must not be
    # able to reach the player's only dump through it.
    if [ ! -f "$state/.config/DK64Recompiled/DK64.z64" ]; then
      echo "first run: handing the port its copy of the ROM" >&2
      cp -f "$target" "$state/.config/DK64Recompiled/DK64.z64"
    fi
  '';
}
