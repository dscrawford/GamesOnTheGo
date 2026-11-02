# Super Mario 64 in the browser — sm64ex's WebGL build, compiled to WebAssembly.
#
# Like the sm64coopdx port, the decomp bakes Nintendo's assets in at compile
# time, so nothing here holds them: this is the *builder*, and it runs outside
# the store against a ROM the caller has (`gotg port worker`, on a request from
# the web app). Its output is one JavaScript file -- SINGLE_FILE, the wasm
# inlined -- that the ports store keeps per user and the web app's player page
# runs. Never shared: the file is the game's data.
#
# sm64ex rather than coopdx: coopdx has no web target, and sm64ex's main loop
# already yields to the browser every frame, so no Asyncify.
#
#   usage: gotg-web-sm64ex <rom: .z64, a .zip, or a directory holding one> <out.js>
{ pkgs, lib }:
let
  src = pkgs.fetchFromGitHub {
    owner = "sm64pc";
    repo = "sm64ex";
    rev = "d7ca2c04364a6dd0dac58b47151e04e26887e6f0"; # nightly, 2024-12-17
    hash = "sha256-n3ecY97UB/fdTZpy78CB4DxyHyjK+L6AAuNpvnmVoss=";
  };

  emscripten = pkgs.emscripten;

  # `-s USE_SDL=2` makes emcc download SDL from GitHub at build time; handed
  # the same archive, from the same URL, it does not. Bumping emscripten may
  # move SDL's version: tools/ports/sdl2.py names the one it wants.
  sdl2 = pkgs.fetchurl {
    url = "https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip";
    hash = "sha256-ejwge4UJ7cSH1ljfNXrXZM2FLWj+JI0weyXAdB1S/fA=";
  };
in
pkgs.writeShellApplication {
  name = "gotg-web-sm64ex";
  runtimeInputs = [
    emscripten
    pkgs.stdenv.cc # the asset tools are host programs
    pkgs.gnumake
    pkgs.python3
    pkgs.unzip
    pkgs.util-linux # hexdump, for the asset extractor
    pkgs.coreutils
    pkgs.findutils
    pkgs.gnused
  ];
  text = ''
    rom="''${1:?usage: gotg-web-sm64ex <rom> <out.js>}"
    out="''${2:?usage: gotg-web-sm64ex <rom> <out.js>}"
    rom="$(realpath "$rom")"
    out="$(realpath -m "$out")"
    work="$(mktemp -d)"
    trap 'chmod -R u+w "$work"; rm -rf "$work"' EXIT

    # Emscripten writes its system libraries into its cache the first time
    # they are linked. Kept across builds, keyed by the compiler -- the store
    # copy is read-only, and rebuilding libc for every request is minutes.
    cache_root="''${XDG_CACHE_HOME:-$HOME/.cache}/gotg/emscripten"
    export EM_CACHE="$cache_root/${builtins.baseNameOf emscripten}"
    if [ ! -e "$EM_CACHE/.seeded" ]; then
      rm -rf "$EM_CACHE"
      mkdir -p "$EM_CACHE"
      cp -r ${emscripten}/share/emscripten/cache/. "$EM_CACHE/"
      chmod -R u+w "$EM_CACHE"
      touch "$EM_CACHE/.seeded"
      # A cache for any other compiler is of no use now.
      find "$cache_root" -mindepth 1 -maxdepth 1 ! -name "$(basename "$EM_CACHE")" -exec rm -rf {} +
    fi
    # Emscripten trusts a port only unpacked, beside a marker naming the URL
    # it came from (tools/ports/__init__.py, up_to_date): the archive alone
    # it deletes and downloads again. So unpacked, as it would unpack it.
    sdl2_dir="$EM_CACHE/ports/sdl2"
    if [ "$(cat "$sdl2_dir/.emscripten_url" 2>/dev/null)" != "${sdl2.url}" ]; then
      rm -rf "$sdl2_dir" "$sdl2_dir.tmp"
      mkdir -p "$sdl2_dir.tmp"
      unzip -q ${sdl2} -d "$sdl2_dir.tmp"
      chmod -R u+w "$sdl2_dir.tmp"
      echo "${sdl2.url}" >"$sdl2_dir.tmp/.emscripten_url"
      cp ${sdl2} "$EM_CACHE/ports/sdl2.32.10.zip"
      mv "$sdl2_dir.tmp" "$sdl2_dir"
    fi

    cp -r ${src}/. "$work/src"
    chmod -R u+w "$work/src"
    cd "$work/src"

    # An install is the .zip, or a directory around it when the game has
    # extras attached; either may already be the bare .z64.
    mkdir -p "$work/rom"
    if [ -d "$rom" ]; then
      cp -r "$rom/." "$work/rom/"
    elif [ "''${rom##*.}" = zip ]; then
      unzip -q -o "$rom" -d "$work/rom"
    else
      cp "$rom" "$work/rom/"
    fi
    find "$work/rom" -name '*.zip' -exec unzip -q -o {} -d "$work/rom" \;
    found="$(find "$work/rom" -name '*.z64' -print -quit)"
    [ -n "$found" ] || { echo "no .z64 in $rom" >&2; exit 1; }
    cp "$found" baserom.us.z64

    # The web flags as written are for an Emscripten from 2020: -g4 and
    # EXTRA_EXPORTED_RUNTIME_METHODS are gone, and -lSDL2 is now the port.
    # SINGLE_FILE: one file to store, serve and cache, the wasm inside it.
    # And linked to .js, not the .html sm64ex names: given an .html, this
    # Emscripten inlines the JavaScript into it and writes no .js at all.
    # shellcheck disable=SC2016 # make's $(...), not the shell's
    sed -i \
      -e 's|^EXE := $(BUILD_DIR)/$(TARGET).html$|EXE := $(BUILD_DIR)/$(TARGET).js|' \
      -e 's|^  OPT_FLAGS := -O2 -g4 .*|  OPT_FLAGS := -O2|' \
      -e 's|^  LDFLAGS := -lm -lGL -lSDL2 -no-pie .*|  LDFLAGS := -lm -lGL -s USE_SDL=2 -s INITIAL_MEMORY=64MB -s ALLOW_MEMORY_GROWTH=1 -s SINGLE_FILE=1 -s ENVIRONMENT=web -s "EXPORTED_RUNTIME_METHODS=[\x27callMain\x27]"|' \
      Makefile
    # shellcheck disable=SC2016 # make's $(...), not the shell's
    if ! grep -q 'SINGLE_FILE=1' Makefile || ! grep -qxF 'EXE := $(BUILD_DIR)/$(TARGET).js' Makefile; then
      echo "sm64ex's Makefile moved: the web flags were not patched" >&2
      exit 1
    fi

    make -j"$(nproc)" TARGET_WEB=1 >"$work/build.log" 2>&1 || {
      tail -40 "$work/build.log" >&2
      exit 1
    }
    mkdir -p "$(dirname "$out")"
    cp build/us_web/sm64.us.f3dex2e.js "$out.part"
    mv "$out.part" "$out"
    echo "built $out" >&2
  '';
}
