# The Harkinian ports on Windows (docs/windows.md, "Ship of Harkinian"):
# each plain game and its rando carries a facet whose exe is in the port's
# official build, which starts in the directory the Linux save globs are
# written against, and is the same version as the port Linux runs; a
# split-screen variant says why it has none.
{ pkgs, packages }:
let
  inherit (pkgs) lib;
  win = import ../windows/emulators { inherit pkgs; };

  # env -> the Windows build, its exe, the port's data directory under
  # {state}, and the version the Linux side runs.
  soh = {
    port = win.shipwright;
    exe = "soh.exe";
    data = "data/soh";
    linux = (pkgs.callPackage ../../pkgs/shipwright { }).version;
    firstRun = true;
  };
  twoShip = {
    port = win."2ship";
    exe = "2ship.exe";
    data = "data/2ship";
    linux = pkgs._2ship2harkinian.version;
    firstRun = true;
  };
  supported = {
    env-n64-usa_legend_of_zelda_ocarina_of_time_rev2 = soh;
    env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-rando = soh;
    env-n64-usa_legend_of_zelda_ocarina_of_time_master_quest = soh;
    env-n64-usa_legend_of_zelda_majoras_mask = twoShip;
    env-n64-usa_legend_of_zelda_majoras_mask-rando = twoShip;
    env-n64-usa_super_smash_bros = {
      port = win.battleship;
      exe = "BattleShip.exe";
      data = "data/BattleShip";
      linux = packages.battleship.version;
      firstRun = false;
    };
  };
  unsupported = [
    "env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-2p"
    "env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-3p"
    "env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-4p"
  ];

  w = n: packages.${n}.gotgWindows;
  m = n: (w n).manifest;
  test =
    n: name: expr: expected:
    lib.nameValuePair "test-${n}-${name}" { inherit expr expected; };

  failures = lib.runTests (
    lib.listToAttrs (
      lib.concatLists (
        lib.mapAttrsToList (n: e: [
          (test n "supported" (w n).supported true)
          (test n "manifest-is-json" (builtins.isString (builtins.toJSON (m n))) true)
          (test n "exe" (m n).exe "emulator/${e.exe}")
          (test n "emulator" (w n).emulator.outPath e.port.outPath)
          (test n "same-version-as-linux" e.port.version e.linux)
          (test n "cwd" (m n).cwd "{state}/${e.data}")
          # Started in {state}/<data>, it writes where the Linux globs look.
          (test n "saves-under-data" (lib.all (lib.hasPrefix "${e.data}/") (m n).saves) true)
          (test n "seed-under-data" (lib.all (lib.hasPrefix "${e.data}/") (
            lib.attrNames (m n).config_files
          )) true)
          (test n "no-fullscreen-flag" (m n).fullscreen_flag null)
          (test n "first-run" ((m n).first_run != null) e.firstRun)
          (test n "rom-once" (
            if e.firstRun then
              (m n).first_run.args == [ "{target}" ]
              && lib.all (lib.hasPrefix "{state}/${e.data}/") (m n).first_run.unless_exists
            else
              lib.any (a: a ? copy && a.copy.from == "{target}" && a.copy.once) (m n).actions
          ) true)
        ]) supported
        ++ map (n: [
          (test n "has-reason" ((w n).supported == false && (w n).reason != "") true)
        ]) unsupported
      )
    )
  );
in
pkgs.runCommand "check-windows-harkinian" { } (
  if failures == [ ] then
    lib.concatStrings (
      lib.mapAttrsToList (n: e: ''
        [ -f ${e.port}/${e.exe} ] || {
          echo "${n}: ${e.exe} is not in ${e.port}" >&2
          exit 1
        }
      '') supported
    )
    + "touch $out\n"
  else
    ''
      echo ${lib.escapeShellArg (builtins.toJSON failures)} >&2
      exit 1
    ''
)
