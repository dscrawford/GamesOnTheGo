# EmulatorJS for the browser player (/play/ejs/, GOTG_EJS_DIR): the pinned
# upstream release, trimmed to the cores the service maps. The core list is
# read from src/gotg/service/play_systems.json -- the same file the service
# reads for /play/plan -- so a system added there is packaged here, and a
# core missing from the release fails this build instead of a game in a tab.
#
# Kept per core: the plain and "legacy" (WebGL 1) builds and its report. The
# threaded builds are dropped: EmulatorJS only uses them with EJS_threads and
# cross-origin isolation (COOP/COEP), which the player does not turn on.
# This is a prebuilt release, not a from-source build of the cores.
{
  lib,
  stdenvNoCC,
  fetchurl,
  p7zip,
}:

let
  systems = builtins.fromJSON (builtins.readFile ../src/gotg/service/play_systems.json);
  cores = lib.unique (lib.mapAttrsToList (_: s: s.core) systems.browser);
in
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "emulatorjs";
  version = "4.2.3";

  src = fetchurl {
    url = "https://github.com/EmulatorJS/EmulatorJS/releases/download/v${finalAttrs.version}/${finalAttrs.version}.7z";
    hash = "sha256-B9RRvAb6OtBKsw2blOtjrDStC6vuUtYDV7ACvejzhQs=";
  };

  nativeBuildInputs = [ p7zip ];
  dontConfigure = true;
  dontBuild = true;
  # Served to browsers byte for byte: no shebang or RPATH patching.
  dontFixup = true;

  unpackPhase = ''
    runHook preUnpack
    mkdir release
    7z x -y -orelease "$src" > /dev/null
    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/data/cores/reports"
    cp release/LICENSE "$out/"
    # Everything but the cores (loader, emulator, extractors, localization).
    for entry in release/data/*; do
      [ "$(basename "$entry")" = cores ] || cp -r "$entry" "$out/data/"
    done
    cp release/data/cores/cores.json "$out/data/cores/"
    for core in ${lib.escapeShellArgs cores}; do
      for build in wasm legacy-wasm; do
        file="release/data/cores/$core-$build.data"
        [ -f "$file" ] || { echo "EmulatorJS ${finalAttrs.version} has no $core-$build.data" >&2; exit 1; }
        cp "$file" "$out/data/cores/"
      done
      cp "release/data/cores/reports/$core.json" "$out/data/cores/reports/"
    done
    test -f "$out/data/loader.js"
    runHook postInstall
  '';

  passthru = { inherit cores; };

  meta = {
    description = "EmulatorJS ${finalAttrs.version}, trimmed to the cores GOTG's browser player maps";
    homepage = "https://emulatorjs.org";
    # EmulatorJS is GPL-3.0; the cores keep their own licences (docs/browser-play.md).
    license = lib.licenses.gpl3Only;
    platforms = lib.platforms.all;
  };
})
