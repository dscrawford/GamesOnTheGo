# pyisotools — extracts and, crucially, *rebuilds* GameCube disc images.
#
# Packaged because BetterSunshineEngine's README names it as one of only two
# supported ways to rebuild a patched disc. That is not a stylistic preference:
# wiimms-iso-tools produced an image with Wii partition structures and its own
# regenerated boot.bin, which Dolphin loaded and the game then failed to boot.
# A rebuilder has to preserve the modified disc header the mod ships.
{
  lib,
  python3Packages,
  fetchFromGitHub,
}:

python3Packages.buildPythonApplication rec {
  pname = "pyisotools";
  version = "2.4.8";
  pyproject = true;

  src = fetchFromGitHub {
    owner = "JoshuaMKW";
    repo = "pyisotools";
    tag = "v${version}";
    hash = "sha256-rBtV//270GYTQcS1JJ+atG+MQLs7Gzi9rMVZQV3fZOM=";
  };

  build-system = [ python3Packages.setuptools ];

  # distutils went away in Python 3.12; packaging.Version is its replacement and
  # is used the same way. Only the GUI's update check touches it, but the import
  # is at module scope, so it breaks the CLI too.
  postPatch = ''
    substituteInPlace pyisotools/gui/updater.py \
      --replace-fail "from distutils.version import LooseVersion" \
                     "from packaging.version import Version as LooseVersion"
  '';

  # requirements.txt also lists qdarkstyle, pyinstaller and pylint — a theme for
  # the GUI and two build-time tools. None are needed to rebuild a disc, and the
  # dependency check is relaxed rather than pulling them in.
  dontCheckRuntimeDeps = true;

  dependencies = with python3Packages; [
    (callPackage ./dolreader.nix { })
    beautifulsoup4
    chardet
    packaging
    pillow
    pygithub
    pyside6
    sortedcontainers
  ];

  # setup.py declares no console script, so the module is runnable only as
  # `python -m pyisotools`. Written into bin/ before the Python wrapper hook
  # runs, so it picks up the dependency path like any normal entry point.
  postInstall = ''
    mkdir -p $out/bin
    cat > $out/bin/pyisotools <<EOF
    #!${python3Packages.python.interpreter}
    import sys
    from pyisotools.__main__ import main
    sys.exit(main())
    EOF
    sed -i 's/^    //' $out/bin/pyisotools
    chmod +x $out/bin/pyisotools
  '';

  # No test suite; the meaningful check is that a rebuilt image boots, which
  # needs a game and an emulator.
  doCheck = false;
  pythonImportsCheck = [ "pyisotools.iso" ];

  meta = {
    description = "Extract and rebuild GameCube disc images";
    homepage = "https://github.com/JoshuaMKW/pyisotools";
    mainProgram = "pyisotools";
  };
}
