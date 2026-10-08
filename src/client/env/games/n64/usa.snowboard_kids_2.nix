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
# What every N64Recomp port shares -- it ignores XDG_CONFIG_HOME, the ROM is
# stored by the port once, the pad it wants -- is in emulators/recomp-port.nix.
# This file is the part that is Snowboard Kids 2's: the name of its stored ROM.
{
  pkgs,
  lib,
  helpers,
  gotgPkgs,
  ...
}:
helpers.recompPort {
  emulator = gotgPkgs.snowboardkids2recomp;
  bin = "SnowboardKids2Recompiled";
  dir = "SnowboardKids2Recompiled";
}
// {
  preLaunch = ''
    export HOME="$state"
    config="$state/.config/SnowboardKids2Recompiled"
    mkdir -p "$config/mods"

    # The ROM, put where the port keeps the copy it makes for itself, so its
    # picker never opens. N64ModernRuntime stores a picked ROM as
    # `<game_id>.z64` beside the settings — GameEntry::stored_filename() is
    # `game_id + u8".z64"` — and check_all_stored_roms() looks there before
    # asking anyone anything.
    #
    # The id is the long one. Every port of this kind carries two, and the
    # short one is the wrong one: upstream registers
    # `.game_id = u8"snowboardkids2.n64.us"` beside
    # `.mod_game_id = "snowboardkids2"`, and it is the former that names the
    # file. A name that is merely wrong fails silently — the runtime finds
    # nothing to check, leaves the file alone, and the launcher still says
    # "Select ROM", which is exactly what a copy called snowboardkids2.z64
    # did here.
    #
    # A copy rather than a link: this is the port's file to manage — it
    # deletes it outright when the hash does not match — and it must not be
    # able to reach the player's only dump through it.
    if [ ! -f "$config/snowboardkids2.n64.us.z64" ]; then
      echo "first run: handing the port its copy of the ROM" >&2
      cp -f "$target" "$config/snowboardkids2.n64.us.z64"
    fi
  '';
}
