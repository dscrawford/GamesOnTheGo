# The Windows facet (docs/windows.md): it rides in passthru, so setting one
# must not move an environment's drvPath -- the Linux path stays exactly as
# it was -- and a game that changes how it starts must not inherit a
# platform's facet that would start the wrong thing.
{ pkgs }:
let
  inherit (pkgs) lib;
  mkEnv = import ../../src/client/env/machinery/lib.nix { inherit pkgs lib; };
  emu = pkgs.runCommand "fake-emulator-windows" { } "mkdir $out; touch $out/emu.exe";
  plain = {
    name = "env-test";
    emulator = pkgs.hello;
    args = [
      "{fullscreen}"
      "--saves={state}/saves/"
      "{target}"
    ];
    saves = [ "saves/**" ];
    configFiles."emu/settings.ini" = "a = 1\n";
  };
  facet = {
    emulator = emu;
    exe = "emu.exe";
    configFiles."settings.ini" = "a = 1\n";
  };
  without = mkEnv plain;
  with' = mkEnv (plain // { windows = facet; });
  reasoned = mkEnv (plain // { windows.reason = "not yet"; });

  # default.nix's merge, through a scratch env tree: a platform with a facet,
  # a game that only renames itself, and one that swaps the emulator.
  envDir = pkgs.runCommand "windows-facet-envs" { } ''
    mkdir -p $out/machinery $out/games/fake
    cp ${../../src/client/env/machinery}/*.nix $out/machinery/
    cp ${../../src/client/env/default.nix} $out/default.nix
    cp ${../../src/client/env/foreign-gl.nix} $out/foreign-gl.nix
    cp ${../../src/client/env/machine.sh} $out/machine.sh
    cat >$out/fake.nix <<'NIX'
    { pkgs, ... }: {
      emulator = pkgs.hello;
      saves = [ "saves/**" ];
      windows = { emulator = pkgs.runCommand "e" { } "mkdir $out; touch $out/emu.exe"; exe = "emu.exe"; };
    }
    NIX
    echo '{ ... }: { title = "Renamed"; }' >$out/games/fake/usa.renamed.nix
    echo '{ pkgs, ... }: { emulator = pkgs.cowsay; }' >$out/games/fake/usa.ported.nix
    echo '{ pkgs, ... }: { emulator = pkgs.cowsay; windows.reason = "own words"; }' >$out/games/fake/usa.said.nix
  '';
  envs = import "${envDir}/default.nix" {
    inherit pkgs lib;
    tools = { };
  };
  w = n: envs.${n}.gotgWindows;

  failures = lib.runTests {
    testFacetLeavesDrvPathAlone = {
      expr = with'.drvPath;
      expected = without.drvPath;
    };
    testReasonLeavesDrvPathAlone = {
      expr = reasoned.drvPath;
      expected = without.drvPath;
    };
    testNoFacetIsUnsupported = {
      expr = without.gotgWindows;
      expected = {
        supported = false;
        reason = "env-test has no Windows build yet";
      };
    };
    testReasonIsKept = {
      expr = reasoned.gotgWindows.reason;
      expected = "not yet";
    };
    testManifest = {
      expr = {
        inherit (with'.gotgWindows.manifest)
          exe
          args
          config_files
          saves_dirs
          config_root
          ;
      };
      expected = {
        exe = "emulator/emu.exe";
        args = plain.args;
        config_files."settings.ini" = "config/settings.ini";
        saves_dirs = [ "saves" ];
        config_root = "state";
      };
    };
    testManifestIsJson = {
      expr = builtins.isString (builtins.toJSON with'.gotgWindows.manifest);
      expected = true;
    };
    testPlatformHasFacet = {
      expr = (w "env-fake").supported;
      expected = true;
    };
    testHarmlessPatchInherits = {
      expr = (w "env-fake-usa_renamed").supported;
      expected = true;
    };
    testLaunchPatchDropsFacet = {
      expr = w "env-fake-usa_ported";
      expected = {
        supported = false;
        reason = "this game changes emulator and states no Windows facet of its own";
      };
    };
    testPatchSaysItsOwn = {
      expr = (w "env-fake-usa_said").reason;
      expected = "own words";
    };
  };
in
pkgs.runCommand "check-windows-facet" { } (
  if failures == [ ] then
    "touch $out"
  else
    ''
      echo ${lib.escapeShellArg (builtins.toJSON failures)} >&2
      exit 1
    ''
)
