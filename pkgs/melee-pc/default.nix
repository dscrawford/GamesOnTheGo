# melee-pc — the native Super Smash Bros. Melee port.
#
# Built from doldecomp/melee, which finished after six years, on top of
# aurora: a GX/OS/PAD/DVD/CARD/THP compatibility layer that renders through
# Dawn's WebGPU (Vulkan here) with SDL3 for windowing and pads. Not emulation
# and not a recompilation — the decompiled game code compiled for x86-64.
#
# Packaged from the upstream Linux tarball the same way pkgs/dk64recomp and
# pkgs/snowboardkids2recomp are: patched onto nixpkgs' libraries rather than
# rebuilt. Building it here would mean GCC specifically (the decomp relies on
# scalar_storage_order, which only GCC implements) plus aurora fetching its
# own Dawn, SDL3 and nod prebuilts mid-build — a fixed-output derivation's
# worth of network for a binary upstream already publishes.
#
# It ships no game data. The disc is the player's and is passed on the
# command line; see
# src/client/env/games/gamecube/usa.super_smash_bros_melee_rev2.nix.
#
# Still beta: upstream's own status list has VS, Classic, Adventure,
# All-Star, Training and Stadium working end to end, with online rollback
# play explicitly not implemented yet.
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  openssl,
  curl,
  libpng,
  sqlite,
  vulkan-loader,
  libglvnd,
  libxkbcommon,
  wayland,
  libx11,
  libxext,
  libxrandr,
  libxcursor,
  libxi,
  libxfixes,
  libxscrnsaver,
  libxtst,
  alsa-lib,
  libpulseaudio,
  udev,
  libdecor,
}:
stdenv.mkDerivation (finalAttrs: {
  pname = "melee-pc";
  version = "0.1.7-beta";

  src = fetchurl {
    url =
      "https://github.com/999sian/melee-pc/releases/download/"
      + "v${finalAttrs.version}/melee-linux-x86_64.tar.gz";
    hash = "sha256-EE/xB2WF/F4U0JWx55xRoKziJGqQS9mVo7xdWqpzLtk=";
  };

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];

  # SDL3 is linked into the binary rather than beside it, so what is named
  # here is what the binary asks the dynamic loader for, plus the stack SDL
  # dlopens once it is running.
  buildInputs = [
    openssl
    curl
    libpng
    sqlite
    vulkan-loader
    libglvnd
    libxkbcommon
    wayland
    libdecor
    libx11
    libxext
    libxrandr
    libxcursor
    libxi
    libxfixes
    libxscrnsaver
    libxtst
    alsa-lib
    libpulseaudio
    udev
  ];

  # Two that are asked for and never used here. libsteam_api is the Steam
  # overlay, which this is not launched through; libGLES_CM is the old
  # OpenGL ES 1 soname, which nothing has shipped in years -- SDL3 looks for
  # both and does without either. Rendering goes through Vulkan regardless.
  autoPatchelfIgnoreMissingDeps = [
    "libsteam_api.so"
    "libGLES_CM.so.1"
  ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/melee-pc
    cp -r melee resources initial_pipeline_cache.db melee.png melee.desktop \
      $out/share/melee-pc/
    chmod +x $out/share/melee-pc/melee

    # No --chdir, matching upstream's own run.sh: it resolves resources from
    # the executable's directory, so the working directory is the caller's to
    # choose. Vulkan needs the loader told where it is, which outside a NixOS
    # session it otherwise cannot find, and SDL3 dlopens its windowing and
    # audio libraries by soname at runtime rather than linking them.
    makeWrapper $out/share/melee-pc/melee $out/bin/melee \
      --suffix LD_LIBRARY_PATH : ${
        lib.makeLibraryPath [
          vulkan-loader
          libglvnd
          libxkbcommon
          wayland
          libdecor
          libx11
          libxext
          libxrandr
          libxcursor
          libxi
          libxfixes
          libxscrnsaver
          libxtst
          alsa-lib
          libpulseaudio
          udev
        ]
      }
    runHook postInstall
  '';

  meta = {
    description = "Native PC port of Super Smash Bros. Melee on the doldecomp/aurora stack";
    homepage = "https://github.com/999sian/melee-pc";
    # The port code is GPL-3.0-or-later, but the decompiled game code it is
    # built from is not licensed and cannot be relicensed, so the artefact as
    # a whole is not redistributable. unfree is the honest label.
    license = lib.licenses.unfree;
    platforms = [ "x86_64-linux" ];
    mainProgram = "melee";
  };
})
