# GameCube and Wii on Windows: both platforms carry a Dolphin facet whose exe
# is really in the official build, whose save globs sit under the --user
# directory it passes, and every game that starts differently says why not.
{ pkgs, packages }:
let
  inherit (pkgs) lib;
  supported = [
    "env-gamecube"
    "env-wii"
  ];
  games = lib.filter (n: lib.hasPrefix "env-gamecube-" n || lib.hasPrefix "env-wii-" n) (
    lib.attrNames packages
  );
  w = n: packages.${n}.gotgWindows;
  m = n: (w n).manifest;
  userDir = "{state}/data/dolphin-emu";

  failures = lib.runTests (
    lib.listToAttrs (
      lib.concatMap (n: [
        (lib.nameValuePair "test-${n}-supported" {
          expr = (w n).supported;
          expected = true;
        })
        (lib.nameValuePair "test-${n}-manifest-is-json" {
          expr = builtins.isString (builtins.toJSON (m n));
          expected = true;
        })
        (lib.nameValuePair "test-${n}-user-dir" {
          expr = lib.elem "--user=${userDir}" (m n).args;
          expected = true;
        })
        # The Linux globs, unchanged, must land inside Dolphin's user dir.
        (lib.nameValuePair "test-${n}-saves-under-user" {
          expr = lib.all (lib.hasPrefix "data/dolphin-emu/") ((m n).saves ++ [ (m n).states.files ]);
          expected = true;
        })
        (lib.nameValuePair "test-${n}-ini-under-user" {
          expr = lib.all (a: lib.hasPrefix "${userDir}/Config/" a.ini.file) (m n).actions;
          expected = true;
        })
        (lib.nameValuePair "test-${n}-exe" {
          expr = (m n).exe;
          expected = "emulator/Dolphin.exe";
        })
      ]) supported
      ++ map (n: {
        name = "test-${n}-has-reason";
        value = {
          expr = (w n).supported == false && lib.isString (w n).reason && (w n).reason != "";
          expected = true;
        };
      }) games
    )
  );
in
pkgs.runCommand "check-windows-dolphin" { } (
  if failures == [ ] then
    lib.concatMapStrings (n: ''
      [ -f ${(w n).emulator}/${lib.removePrefix "emulator/" (m n).exe} ] || {
        echo "${n}: ${(m n).exe} is not in ${(w n).emulator}" >&2
        exit 1
      }
    '') supported
    + "touch $out\n"
  else
    ''
      echo ${lib.escapeShellArg (builtins.toJSON failures)} >&2
      exit 1
    ''
)
