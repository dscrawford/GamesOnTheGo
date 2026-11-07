# A game's library output as a zip that plays on Windows without Nix:
# `nix build <library>#<output>.gotgWindows`, pushed by `gotg port push
# --windows` and downloaded from the web app. See docs/windows.md.
#
# Built on Linux, from the environment's Windows facet (machinery/lib.nix,
# passthru.gotgWindows): gotg.exe, the spec with its "windows" section, the
# emulator's Windows build and the facet's seed files. Like a .run, no game
# data: gotg.exe downloads the game and keeps saves in sync.
{
  lib,
  runCommand,
  writeText,
  zip,
}:
{
  # platform.id[.variant], as the library names the output.
  name,
  # The library spec (lib/library.nix `fields`).
  fields,
  # The environment's passthru.gotgWindows.
  facet,
  # The launcher: a package with bin/gotg.exe (rust/gotg-launch.nix).
  launcher,
}:
if !facet.supported then
  throw "${name}: no Windows build: ${facet.reason}"
else if launcher == null then
  throw "${name}: no Windows launcher in this flake yet (rust/gotg-launch.nix)"
else
  let
    dir = "gotg-${name}";
    spec = writeText "gotg-spec-${name}-windows.json" (
      builtins.toJSON (removeAttrs fields [ "env" ] // { windows = facet.manifest; })
    );
    copyFiles = lib.concatLines (
      lib.mapAttrsToList (rel: src: ''
        mkdir -p "$(dirname ${lib.escapeShellArg rel})"
        cp --no-preserve=mode ${src} ${lib.escapeShellArg rel}
      '') facet.files
    );
  in
  runCommand "${dir}-win64.zip"
    {
      nativeBuildInputs = [ zip ];
      passthru = { inherit spec facet; };
      meta.description = "${name}, as a zip that plays on Windows";
    }
    ''
      mkdir -p ${dir}
      cd ${dir}
      cp ${launcher}/bin/gotg.exe gotg.exe
      cp ${spec} spec.json
      cp -r --no-preserve=mode ${facet.emulator} emulator
      [ -f ${lib.escapeShellArg facet.manifest.exe} ] || {
        echo "bundle: ${facet.manifest.exe} is not in the emulator's Windows build" >&2
        exit 1
      }
      ${copyFiles}
      cd ..
      # Reproducible: one mtime (zip's floor is 1980), sorted entries, no
      # extra attributes.
      find ${dir} -exec touch -h -d @315532800 {} +
      find ${dir} -print | LC_ALL=C sort | zip -X -q -@ "$out"
    ''
