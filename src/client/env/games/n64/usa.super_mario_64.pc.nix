# Super Mario 64 — sm64coopdx, the maintained decomp port: analog free
# camera and frame interpolation past the console's 30fps, still driven by
# this ROM. Not emulation, like the Zelda ports beside it — but unlike them
# the engine cannot extract assets at first run: the decomp bakes them in at
# compile time, so there is no prebuilt binary to ship without shipping
# Nintendo's assets in the store. The kuribo pattern instead: the first
# launch compiles the port against the downloaded ROM, on this machine, into
# this environment's state — a few minutes, once, per machine.
{ pkgs, lib, ... }:
let
  src = pkgs.fetchFromGitHub {
    owner = "coop-deluxe";
    repo = "sm64coopdx";
    tag = "v1.5.1";
    hash = "sha256-AadjXTjUBnSbHP8tRHKvWotW58s5tMUJGtxbdPxYg6E=";
  };

  # A vendored dependency includes <libc.h>, a BSD-ism glibc does not have.
  # The same shim nixpkgs' package uses.
  libcHack = pkgs.writeTextFile {
    name = "sm64coopdx-libc-hack";
    text = ''
      #include <unistd.h>
      #include <string.h>
      #include <pthread.h>
    '';
    destination = "/include/libc.h";
  };

  buildDeps = [
    pkgs.SDL2
    pkgs.zlib
    pkgs.curl
    pkgs.libglvnd
  ];

  builder = pkgs.writeShellApplication {
    name = "gotg-build-sm64coopdx";
    runtimeInputs = [
      pkgs.stdenv.cc
      pkgs.gnumake
      pkgs.python3
      pkgs.SDL2.dev # sdl2-config, which the Makefile asks for the flags
      pkgs.util-linux # hexdump, used by the asset extractor
    ];
    text = ''
      # usage: gotg-build-sm64coopdx <baserom.us.z64> <out-dir>
      rom="$1"
      out="$2"
      work="$(mktemp -d)"
      trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT

      cp -r ${src}/. "$work"
      chmod -R u+w "$work"
      cd "$work"
      cp "$rom" baserom.us.z64
      # stdenv's compiler refuses foreign -march flags; nixpkgs makes the
      # same edit. Literal $(TARGET_ARCH): a Makefile variable, not shell.
      # shellcheck disable=SC2016
      sed -i 's/ -march=$(TARGET_ARCH)//' Makefile

      export CPATH="${libcHack}/include:${lib.makeSearchPathOutput "dev" "include" buildDeps}"
      export LIBRARY_PATH="${lib.makeLibraryPath buildDeps}"
      make -j"$(nproc)" BREW_PREFIX=/not-exist DISCORD_SDK=0 COOPNET=0 TEXTURE_FIX=1

      mkdir -p "$out"
      cp build/us_pc/sm64coopdx "$out/"
      cp -r build/us_pc/dynos build/us_pc/lang build/us_pc/mods build/us_pc/palettes "$out/"
      # Needed at run time too, not only for the build.
      cp "$rom" "$out/baserom.us.z64"
      chmod -R u+w "$out"
    '';
  };

  # The port finds dynos/lang/mods beside its executable, so it runs from the
  # tree the first launch compiled. Store libraries at run time: the binary
  # was linked outside stdenv, so nothing wrote an rpath into it.
  launcher = pkgs.writeShellApplication {
    name = "gotg-sm64coopdx";
    text = ''
      port="''${1:?state directory required}/coopdx"
      shift
      export LD_LIBRARY_PATH="${lib.makeLibraryPath buildDeps}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
      cd "$port"
      # Anything after the state directory belongs to the port: a plain launch
      # passes none, and a co-op one passes which save path, which controller
      # and whether this copy is hosting.
      exec ./sm64coopdx "$@"
    '';
  };
in
{
  emulator = launcher;
  bin = "gotg-sm64coopdx";
  configurable = false;
  isolate = true;
  args = [ "{state}" ];

  path = [
    builder
    pkgs.unzip
  ];

  preLaunch = ''
    port="$state/coopdx"
    if [ ! -x "$port/sm64coopdx" ]; then
      echo "first run: compiling sm64coopdx against this ROM — a few minutes, once" >&2
      chmod -R u+w "$port" 2>/dev/null || true
      rm -rf "$port" "$state/.build"
      mkdir -p "$state/.build"
      unzip -o "$install" -d "$state/.build" >/dev/null
      gotg_rom="$(find "$state/.build" -name '*.z64' | head -1)"
      [ -n "$gotg_rom" ] || { echo "no .z64 inside $install" >&2; exit 1; }
      gotg-build-sm64coopdx "$gotg_rom" "$port"
      rm -rf "$state/.build"
    fi
  '';

  # coopdx keeps saves and its settings under its data dir; the compiled port
  # itself is rebuilt from the ROM on any machine, so it does not travel.
  saves = [ "data/sm64coopdx/**" ];
  saveExcludes = [ "data/sm64coopdx/tex/**" ];
}
