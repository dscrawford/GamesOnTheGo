# Pokémon Emerald Rogue — `gotg play world.pokemon_emerald_version rogue`.
#
# A roguelike overhaul built on the pokeemerald decompilation. Upstream
# distributes it as a UPS patch (see their export_ups.sh, which diffs their
# build against vanilla Emerald); this builds the ROM from source instead,
# because a patch is the one thing they do *not* publish at a URL anything can
# pin — it lives behind forum and Discord downloads.
#
# **It needs no base ROM.** There is no `baserom` anywhere in its Makefile: the
# decomp carries the assets, so `make` alone produces a complete Emerald
# derivative. That is the difference from the sm64coopdx variant next door,
# whose whole reason for compiling on the player's machine is that its decomp
# extracts assets from the ROM you own. Nothing here can honestly claim that,
# and the plain `world.pokemon_emerald_version` under ares is untouched.
#
# The first launch compiles about a thousand translation units. That is minutes
# on a desktop with every core and considerably worse on a handheld, behind a
# progress dialog with no terminal to read errors in — so run `gotg install`
# once from a terminal, as the README says for every environment that builds.
#
# Four things this has to work around, each found by hitting it:
#
#   * poryscript is not in nixpkgs and the Makefile hardcodes its path. It is
#     fetched here at the version init_deps.sh pins, and it is a static Go
#     binary, so it needs no patching to run.
#   * mapjson vendors json11, which predates GCC 13 dropping the transitive
#     <cstdint>. Its Makefile assigns CXXFLAGS with `:=`, so the flag has to
#     arrive on the command line to win.
#   * Three sources `#include "string.h"` — quoted — meaning the C standard
#     header. Compilation is fine either way, but scaninc reads a quoted
#     include as project-local and emits src/string.h as a prerequisite that
#     does not exist, which stops make before a single object is linked.
#   * libpng and zlib are named by store path rather than found on a search
#     path. A loose search picks up the ARM-cross zlib that the toolchain below
#     drags in, and the host linker then rejects the x86-64 library as
#     "incompatible" — an error that reads like a toolchain collision.
{
  pkgs,
  lib,
  base,
  ...
}:
let
  version = "EX-v2.1";

  src = pkgs.fetchFromGitHub {
    owner = "Pokabbie";
    repo = "pokeemerald-rogue";
    tag = version;
    hash = "sha256-3eb8GkCG427m6eYQ8DyFaI+R3sBxUeqIEFMbzhOn7pU=";
  };

  # The version init_deps.sh pins. Statically linked Go, so it runs on NixOS
  # as it ships — the usual foreign-binary patching is not needed.
  poryscript = pkgs.fetchurl {
    url = "https://github.com/huderlem/poryscript/releases/download/3.0.2/poryscript-linux.zip";
    hash = "sha256-EIDCxIoBIvcuMyXgQZ6laJzNQL9L4/+iuxze6Zq/U+Y=";
  };

  # The host half. Kept apart from the ARM half below because the two compile
  # different things: tools/ runs here, the ROM runs on the cartridge.
  hostDeps = [
    pkgs.libpng
    pkgs.zlib
  ];
in
{
  # Its own state directory: a roguelike's saves have nothing to say to a
  # vanilla Emerald playthrough, and the built ROM lives here too.
  isolate = true;

  title = "Pokémon Emerald Rogue";

  # Every arm-none-eabi binary is prefixed, so both toolchains share one PATH
  # without the cross linker shadowing the host one. Checked, not assumed.
  path = [
    pkgs.unzip
    pkgs.gnumake
    pkgs.python3
    pkgs.pkg-config
    pkgs.stdenv.cc
    pkgs.pkgsCross.arm-embedded.buildPackages.gcc
    pkgs.pkgsCross.arm-embedded.buildPackages.binutils
  ];

  preLaunch = ''
    rom="$state/rogue/pokeemerald.gba"

    if [ ! -f "$rom" ]; then
      echo "first run: compiling Pokémon Emerald Rogue ${version} — several minutes, once" >&2
      work="$(mktemp -d)"
      # Expanded now on purpose: the path is gone by trap time.
      # shellcheck disable=SC2064
      trap "chmod -R u+w '$work' 2>/dev/null || true; rm -rf '$work'" EXIT

      cp -r ${src}/. "$work"
      chmod -R u+w "$work"
      cd "$work"

      # Exactly where the Makefile looks; it takes no override for this.
      mkdir -p tools/poryscript
      unzip -q -o ${poryscript} -d tools/poryscript
      chmod +x tools/poryscript/poryscript-linux/poryscript

      sed -i 's|#include "string\.h"|#include <string.h>|' \
        src/new_game.c src/rogue_multiplayer.c src/rogue_debug.c

      export PKG_CONFIG_PATH="${pkgs.libpng.dev}/lib/pkgconfig"
      export LIBRARY_PATH="${lib.makeLibraryPath hostDeps}"

      # mapjson first and on its own, so the cstdint flag reaches it without
      # being imposed on every other tool's build.
      make -C tools/mapjson CXXFLAGS="-Wall -std=c++11 -O2 -include cstdint"
      make tools -j"$(nproc)"
      make -j"$(nproc)"

      mkdir -p "$state/rogue"
      cp pokeemerald.gba "$rom"
      cd - >/dev/null
    fi
  '';

  # The platform's own arguments with only the target swapped: the built ROM
  # rather than the downloaded one. A copied list would have frozen whatever
  # the base said the day it was copied — which is exactly how this variant
  # missed the BIOS --setting gba.nix grew, and greeted its first launch with
  # ares' firmware dialog while bios.zip sat already fetched in its state.
  args = map (a: if a == "{target}" then "{state}/rogue/pokeemerald.gba" else a) base.args;

  inherit (base) saves saveExcludes;
}
