# The N64Recomp / RT64 static recompilations, as environments.
#
# Donkey Kong 64 and Snowboard Kids 2 were two files that agreed on everything
# below and differed in four names -- the package, the binary, the directory
# the port keeps its settings in, and what each did before launch (DK64 installs
# two mods; Snowboard Kids 2 has none, and names its stored ROM differently).
# So those four stay in the game's file and the rest is here: what is true of
# the shared N64ModernRuntime, once.
#
#   * A native port, not the platform emulator: upstream ships a Linux build,
#     patched onto nixpkgs' libraries in pkgs/. `gotg play <id> emulate` is the
#     way back to ares when one misbehaves.
#   * It ignores XDG_CONFIG_HOME. Run with XDG_CONFIG_HOME and HOME at
#     different directories and the settings land in $HOME/.config/<dir>
#     regardless. `isolate` only exports the XDG variables, so redirecting HOME
#     is what actually keeps it out of the player's real home. (HOME is set
#     here and again by the game's preLaunch, which also creates the directory.)
#   * A wired Xbox 360 pad. It reads its own controller database
#     (recompcontrollerdb.txt beside the binary) and a clone mirroring a Steam
#     Controller is in no database at all; 045e:028e is the one every SDL maps
#     by heart. See docs/requests/look-like-an-xbox-pad.md.
#   * Settings are in-game -- the launcher's own Settings entry -- so there is
#     no configuration screen to open, and `gotg configure` opening one would
#     just start the game.
#   * The ROM is not a command-line argument; the launcher holds it. The port
#     reads a bare .z64, not the No-Intro zip it travels as, so both handlers
#     (one zip is single_file from the games-root pass and no_intro_set from
#     the DAT path) unzip and place the tree. The matching half is
#     data/overrides.json marking the entry `unzip`, which is what makes
#     {target} the .z64 inside it.
#   * What travels between machines: saves, the settings beside them and
#     mod_config (which mods are switched on is a choice, not a derived
#     file). Not the mods -- the store refetches them -- and not the ROM
#     copy the launcher stored: nothing in `saves` matches a .z64 today, so
#     that exclude guards a widening of the glob, and a copyrighted ROM must
#     never start syncing between machines.
{ pkgs, steps }:
{
  recompPort =
    {
      emulator,
      bin,
      # The directory under ~/.config the port writes to.
      dir,
    }:
    let
      unpacked = [
        steps.unzip
        steps.placeTree
      ];
    in
    {
      inherit emulator bin;
      nativePort = true;
      padIdentity = "xbox360";
      configurable = false;
      isolate = true;
      args = [ ];
      recipes = {
        single_file = unpacked;
        no_intro_set = unpacked;
      };
      env.HOME = "{state}";
      path = [ pkgs.unzip ];
      saves = [
        ".config/${dir}/saves/**"
        ".config/${dir}/*.json"
        ".config/${dir}/mod_config/**"
      ];
      saveExcludes = [
        ".config/${dir}/mods/**"
        ".config/${dir}/*.z64"
      ];
    };
}
