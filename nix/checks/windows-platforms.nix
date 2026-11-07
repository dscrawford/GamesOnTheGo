# The platforms that have a Windows build keep it (docs/windows.md): their
# environment's facet is supported, the exe its manifest names is in the
# emulator package it ships, and a game of theirs that has none says why.
#
# Builds each listed emulator's Windows package (a pinned upstream zip,
# unpacked) to look inside it; nothing is compiled.
{ pkgs, packages }:
let
  inherit (pkgs) lib;

  # A platform whose Windows build is meant to exist. Add yours.
  platforms = {
    nes = "ares.exe";
    snes = "ares.exe";
    gb = "ares.exe";
    gbc = "ares.exe";
    gba = "ares.exe";
    n64 = "ares.exe";
    genesis = "ares.exe";
  };
  # Games that must keep their platform's Windows build, not just state why
  # they have none.
  games = [
    "env-snes-world_super_metroid"
  ];

  facetOf = name: packages.${name}.gotgWindows;
  family =
    platform:
    lib.filterAttrs (
      name: _: name == "env-${platform}" || lib.hasPrefix "env-${platform}-" name
    ) packages;

  supportedNames = map (p: "env-${p}") (lib.attrNames platforms) ++ games;

  failures =
    lib.concatMap (
      name:
      let
        f = facetOf name;
      in
      if !f.supported then
        [ "${name}: no Windows build: ${f.reason}" ]
      else
        lib.optional (lib.elem "--pseudofullscreen" f.manifest.args) "${name}: --pseudofullscreen is X11's workaround"
        ++ lib.optional (
          f.manifest.fullscreen_flag == "--pseudofullscreen"
        ) "${name}: fullscreen_flag is X11's workaround"
    ) supportedNames
    ++ lib.concatMap (
      p:
      lib.concatLists (
        lib.mapAttrsToList (
          name: env:
          lib.optional (
            !env.gotgWindows.supported && (env.gotgWindows.reason or "") == ""
          ) "${name}: no Windows build and no reason"
        ) (family p)
      )
    ) (lib.attrNames platforms)
    ++ lib.concatMap (
      p:
      let
        m = (facetOf "env-${p}").manifest or { };
      in
      lib.optional (
        (facetOf "env-${p}").supported && m.exe != "emulator/${platforms.${p}}"
      ) "env-${p}: exe is ${m.exe}, expected emulator/${platforms.${p}}"
    ) (lib.attrNames platforms);

  # The exe, looked for in the package that ships as emulator/.
  exeTests = lib.concatMapStrings (
    name:
    let
      f = facetOf name;
      inside = lib.removePrefix "emulator/" f.manifest.exe;
    in
    lib.optionalString f.supported ''
      [ -f ${f.emulator}/${lib.escapeShellArg inside} ] || {
        echo "${name}: ${f.manifest.exe} is not in ${f.emulator}" >&2
        exit 1
      }
    ''
  ) supportedNames;
in
pkgs.runCommand "check-windows-platforms" { } (
  if failures == [ ] then
    ''
      ${exeTests}
      touch $out
    ''
  else
    ''
      echo ${lib.escapeShellArg (lib.concatStringsSep "\n" failures)} >&2
      exit 1
    ''
)
