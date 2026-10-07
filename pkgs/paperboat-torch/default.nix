# Torch, Harbour Masters' asset extractor, as PaperBoat builds it -- so
# pm64.o2r is made from the player's ROM by a command, with no wizard.
#
# PaperBoat links Torch in and drives it from two popups no headless launch
# can answer ("No O2R files found. Generate one now?", "ROMs found..."). A
# first launch behind them on every machine was the cost, and syncing the
# archive between machines (it is 40 MB) was not the answer anybody wanted.
# Torch's own `o2r` subcommand does the same extraction from the same
# config.yml and asset yamls PaperBoat ships; BattleShip already runs it
# this way (src/client/env/games/n64/usa.super_smash_bros.nix).
#
# Pinned to the commit PaperBoat's external/torch submodule names at the
# release pkgs/paperboat packages, and configured as PaperBoat's CMakeLists
# configures it: PM64 on, ROM_CRC_BSWAP on. The other games' factories are
# off -- they are not needed, and they are most of the build. PaperBoat's
# extractor also writes a `portVersion` file into the archive; `-u <version>`
# makes Torch write the same one.
#
# Torch's CMake fetches its dependencies with FetchContent; each is fetched
# here instead and handed over as FETCHCONTENT_SOURCE_DIR_*, the way
# nixpkgs' _2ship2harkinian does for the same family.
{
  lib,
  stdenv,
  fetchFromGitHub,
  cmake,
  ninja,
  pkg-config,
  spdlog,
  zlib,
}:
let
  libgfxd = fetchFromGitHub {
    owner = "glankk";
    repo = "libgfxd";
    rev = "96fd3b849f38b3a7c7b7f3ff03c5921d328e6cdf";
    hash = "sha256-dedZuV0BxU6goT+rPvrofYqTz9pTA/f6eQcsvpDWdvQ=";
  };
  dr_libs = fetchFromGitHub {
    owner = "mackron";
    repo = "dr_libs";
    rev = "da35f9d6c7374a95353fd1df1d394d44ab66cf01";
    hash = "sha256-ydFhQ8LTYDBnRTuETtfWwIHZpRciWfqGsZC6SuViEn0=";
  };
  yaml-cpp = fetchFromGitHub {
    owner = "jbeder";
    repo = "yaml-cpp";
    rev = "yaml-cpp-0.9.0";
    hash = "sha256-+FOsPQY44h1g9tEw3O281LkiYKXdW2jnFKw+oTRkhGw=";
  };
  tinyxml2 = fetchFromGitHub {
    owner = "leethomason";
    repo = "tinyxml2";
    rev = "10.0.0";
    hash = "sha256-9xrpPFMxkAecg3hMHzzThuy0iDt970Iqhxs57Od+g2g=";
  };
  zlibSrc = fetchFromGitHub {
    owner = "madler";
    repo = "zlib";
    rev = "v1.3.1";
    hash = "sha256-TkPLWSN5QcPlL9D0kc/yhH0/puE9bFND24aj5NVDKYs=";
  };
in
stdenv.mkDerivation {
  pname = "paperboat-torch";
  version = "0-unstable-2026-09-28";

  src = fetchFromGitHub {
    owner = "HarbourMasters";
    repo = "Torch";
    rev = "72960ca16f4723f96aaf4037b76072c230af8c11";
    hash = "sha256-JnU2HzENkc58c09S5g4x3/HXpDqF7i6mHbQv9v2+HhY=";
  };

  nativeBuildInputs = [
    cmake
    ninja
    pkg-config
  ];
  # The standalone binary asks for a system zlib as well as the static one
  # it builds for its archives.
  buildInputs = [
    spdlog
    zlib
  ];

  cmakeFlags = [
    (lib.cmakeBool "USE_STANDALONE" true)
    (lib.cmakeBool "ROM_CRC_BSWAP" true)
    (lib.cmakeBool "BUILD_PM64" true)
    (lib.cmakeBool "BUILD_SM64" false)
    (lib.cmakeBool "BUILD_MK64" false)
    (lib.cmakeBool "BUILD_SF64" false)
    (lib.cmakeBool "BUILD_FZERO" false)
    (lib.cmakeBool "BUILD_BK64" false)
    (lib.cmakeBool "BUILD_MARIO_ARTIST" false)
    (lib.cmakeBool "BUILD_OOT" false)
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_LIBGFXD" "${libgfxd}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_DR_LIBS" "${dr_libs}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_YAML-CPP" "${yaml-cpp}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_TINYXML2" "${tinyxml2}")
  ];

  # zlib's CMakeLists renames zconf.h inside its own source tree, which the
  # store will not allow: it gets a writable copy.
  preConfigure = ''
    cp -r ${zlibSrc} "$NIX_BUILD_TOP/zlib"
    chmod -R u+w "$NIX_BUILD_TOP/zlib"
    cmakeFlagsArray+=("-DFETCHCONTENT_SOURCE_DIR_ZLIB=$NIX_BUILD_TOP/zlib")
  '';

  # Torch has no install rule: the binary is the build's.
  installPhase = ''
    runHook preInstall
    install -Dm755 torch $out/bin/torch
    runHook postInstall
  '';

  meta = {
    description = "Harbour Masters' asset extractor, configured as PaperBoat links it";
    homepage = "https://github.com/HarbourMasters/Torch";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "torch";
  };
}
