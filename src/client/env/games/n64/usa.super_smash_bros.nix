# Super Smash Bros. — BattleShip, the PC port built on the ssb-decomp-re
# decompilation with libultraship doing rendering, audio and input. Not
# emulation: the assets come out of this ROM once and the game then runs
# natively, four pads plug-and-play, with a two-player co-op of Classic mode
# that the cartridge never had.
#
# Same lineage as the Ocarina of Time and Majora's Mask ports, so it starts
# from the shared helper and changes the two things this port does its own
# way:
#
#   * **Its data files are read from the working directory, not from beside
#     the binary.** Measured: run it from an empty directory and it says
#     "The archive at path ./f3d.o2r does not exist". So the launch has to
#     happen somewhere writable with those files present, which is what the
#     symlink pass below sets up. It does honour XDG_DATA_HOME for its own
#     state, so `isolate` puts that directory inside the game's state and
#     one place serves both.
#
#   * **Extraction needs no click.** The Harkinian ports stage the ROM and
#     hand over to their own first-run wizard; this one ships Torch as a
#     separate program, so the archive can just be built. That makes this
#     the only port here with no manual first launch.
#
# The matching half is data/overrides.json marking the entry `unzip`: the
# port reads a bare .z64, and where it lands has to be known without
# evaluating nix for `gotg list` to work offline.
{
  helpers,
  pkgs,
  gotgPkgs,
  lib,
  ...
}:
let
  port = gotgPkgs.battleship;
  appName = "BattleShip";
  # What the port expects to find in the directory it is started from.
  runtime = [
    "assets"
    "f3d.o2r"
    "gamecontrollerdb.txt"
    "config.yml"
    "yamls"
    "licenses"
  ];
in
helpers.harkinianPort {
  inherit port appName;
  bin = "BattleShip";
  archives = [ "BattleShip.o2r" ];
}
// {
  preLaunch = ''
    run="''${XDG_DATA_HOME:-$HOME/.local/share}/${appName}"
    mkdir -p "$run"

    # Relinked every launch rather than copied once: an upgrade of the port
    # changes the store path, and a stale copy of its shaders beside a new
    # binary is the kind of mismatch that shows up as a black screen.
    ${lib.concatMapStringsSep "\n" (f: ''
      ln -sfn "${port}/share/battleship/${f}" "$run/${f}"
    '') runtime}

    # It resolves its data files relative to here. See the note at the top.
    cd "$run"

    if [ ! -f "$run/BattleShip.o2r" ]; then
      echo "first run: extracting game assets from $target" >&2
      # -s is where config.yml and the asset metadata live, which is the
      # store path; Torch only reads from it. -d is where the archive goes.
      ${port}/bin/torch o2r -s "${port}/share/battleship" -d "$run" "$target"
    fi
  '';
}
