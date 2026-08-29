# Wiimms SZS Tools — the Yaz0/SZS archiver Nintendo's GameCube and Wii games use.
#
# Needed to install BetterSunshineEngine, whose README requires overwriting files
# *inside* the game's params.szs and recompressing it. Skipping that step yields a
# build that boots while quietly using stock values where the mod expects its own,
# which is a worse failure than refusing outright.
#
# Not in nixpkgs, though its sibling wiimms-iso-tools is; this follows that
# package's shape, since both are the same author and the same build system.
{
  lib,
  stdenv,
  fetchurl,
  zlib,
  libpng,
  ncurses,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "wiimms-szs-tools";
  version = "2.41b";

  src = fetchurl {
    url = "https://download.wiimm.de/source/wiimms-szs-tools/wiimms-szs-tools.source-${finalAttrs.version}.txz";
    hash = "sha256-bTuJ7yWDCkiDjmDMZQQ53i9ExA0hV75TdGYluURtv1E=";
  };

  buildInputs = [
    zlib
    libpng # lib-image2.c: the tools convert Nintendo image formats too
    ncurses
  ];

  postPatch = ''
    patchShebangs . || true
    for f in setup.sh Makefile; do
      [ -e "$f" ] && substituteInPlace "$f" --replace-quiet gcc "$CC"
    done
  '';

  enableParallelBuilding = true;

  env.INSTALL_PATH = placeholder "out";

  # Install the tools directly rather than through upstream's install.sh: that
  # script wants to write outside $out and reports success either way, which
  # produced a package containing an empty bin/ and no error at all.
  installPhase = ''
    runHook preInstall

    found=0
    for t in wszst wbmgt wctct wimgt wkclt wkmpt wlect wmdlt wpatt wstrt; do
      if [ -x "$t" ]; then
        install -Dm755 "$t" "$out/bin/$t"
        found=$((found + 1))
      fi
    done
    [ "$found" -gt 0 ] || { echo "no tools were built" >&2; exit 1; }

    runHook postInstall
  '';

  meta = {
    description = "Command line tools to extract, modify and create SZS/Yaz0 archives";
    homepage = "https://szs.wiimm.de/";
    license = lib.licenses.gpl2Plus;
    platforms = lib.platforms.linux;
  };
})
