# Paper Mario — ReCut, a static recompilation on the N64Recomp/RT64 stack,
# driven by this ROM. The same family as the Zelda ports beside it, with one
# difference that shapes everything here: **it is published for Windows
# only**, so this runs it under Wine.
#
# What it is for, from upstream's own feature list rather than from the
# coverage of it: an RT64 renderer with a live graphics menu (F1), and a
# texture pipeline — dump the game's textures, edit them in the bundled Paper
# Atlas Tool, drop them in user/textures/replacements/, toggle them live with
# F2. It ships **no** high-resolution texture pack; it ships the tooling to
# make one. Nothing upstream claims a frame rate above the console's, and the
# only resolution claim on the page is that widescreen does not work yet. So
# this is a modding platform for Paper Mario, not a remaster of it.
#
# That is affordable because of what the release actually is. It is already
# statically recompiled — "the app is already built, and the launcher asks for
# your legally dumped ROM" — so unlike the sm64coopdx variant there is nothing
# to compile on this machine, and unlike Wine-under-Dolphin (see
# usa.super_mario_sunshine.bsmso.nix) nothing here needs to talk to a native
# process. A prefix, the DLLs, and the exe.
#
# RT64 renders through D3D12, which is the one thing worth checking before
# believing any of this works. Wine staging ships its own d3d12.dll and
# d3d12core.dll — vkd3d over winevulkan — so nothing extra is needed. The
# nixpkgs vkd3d-proton package is *not* the answer here: it installs Linux
# .so builtins, not the PE DLLs a prefix wants, so copying from it would
# silently place nothing and the failure would arrive later, as a device that
# would not create.
#
# The ROM never goes through the launcher's picker: the README documents
# `user/pm.n64.us.z64` beside the executable, so the first launch puts it
# there and the picker has nothing to ask.
#
# Two limitations from upstream, deliberately not worked around:
#
#   * Widescreen is broken — "expect visual problems if you enable it", and
#     4:3 is "the intended play path for now". So this leaves it off; the
#     resolution is the win here, not the aspect.
#   * Save states are "an early runtime snapshot system". The in-game saves
#     travel through `gotg saves` as usual; states are excluded, because a
#     half-working snapshot format is not something to sync between machines
#     and call a save.
#
# It is a pre-release (v0.1.2, "still an early working build"). The plain
# `usa.paper_mario` under ares is untouched and remains the one that just
# works.
{ pkgs, lib, ... }:
let
  version = "0.1.2";

  release = pkgs.fetchurl {
    url =
      "https://github.com/SMCGames/Paper-Mario-ReCut/releases/download/"
      + "v${version}/PaperMarioReCut-v${version}-license-compliance.zip";
    # The project publishes this in the release notes; verified against the
    # downloaded artefact rather than copied on trust.
    hash = "sha256-RzS8LhGIajJ92p7O3yUlgpjzxd38GCDo7robZoPrrSo=";
  };

  # 64-bit only, and checked rather than assumed: every PE in the release —
  # the exe, SDL2.dll, dxcompiler.dll, dxil.dll — is "PE32+ ... x86-64", so
  # the wow (32-bit) side buys nothing. It also matters that it is *this*
  # attribute: wineWowPackages is deprecated upstream and, more to the point,
  # is not in the binary cache, so asking for it builds Wine from source.
  wine = pkgs.wine64Packages.stagingFull;

  launcher = pkgs.writeShellApplication {
    name = "gotg-papermario-recut";
    runtimeInputs = [ wine ];
    text = ''
      state="''${1:?state directory required}"
      app="$state/recut/Paper Mario ReCut"

      export WINEPREFIX="$state/prefix"
      # Never the interactive ones: this is launched from Steam, where a
      # dialog nobody can see is indistinguishable from a hang.
      export WINEDLLOVERRIDES="mscoree,mshtml="
      export WINEDEBUG="-all"
      # d3d12 comes from wine staging's own vkd3d; quiet, because this runs
      # under Steam where stdout is a log file nobody reads.
      export VKD3D_DEBUG=none

      cd "$app"
      exec wine PaperMarioReCut.exe "$@"
    '';
  };
in
{
  emulator = launcher;
  bin = "gotg-papermario-recut";
  configurable = false;
  isolate = true;
  args = [ "{state}" ];

  path = [
    pkgs.unzip
    wine
  ];

  preLaunch = ''
    app="$state/recut/Paper Mario ReCut"

    if [ ! -f "$app/PaperMarioReCut.exe" ]; then
      echo "first run: unpacking Paper Mario ReCut ${version}" >&2
      rm -rf "$state/recut"
      mkdir -p "$state/recut"
      unzip -q -o ${release} -d "$state/recut"
      chmod -R u+w "$state/recut"
    fi

    # The prefix is per-environment, so a wedged one is `rm -rf` on this
    # directory rather than anything shared.
    if [ ! -d "$state/prefix/drive_c" ]; then
      echo "first run: preparing the Wine prefix — a minute, once" >&2
      WINEPREFIX="$state/prefix" WINEDLLOVERRIDES="mscoree,mshtml=" WINEDEBUG=-all \
        wineboot --init >/dev/null 2>&1 || true
    fi

    # The ROM, where the README says the app keeps it — so the launcher's
    # "Select ROM" step has nothing left to ask.
    if [ ! -f "$app/user/pm.n64.us.z64" ]; then
      mkdir -p "$app/user"
      rm -rf "$state/.rom"
      mkdir -p "$state/.rom"
      unzip -q -o "$install" -d "$state/.rom"
      gotg_rom="$(find "$state/.rom" -name '*.z64' | head -1)"
      [ -n "$gotg_rom" ] || { echo "no .z64 inside $install" >&2; exit 1; }
      cp "$gotg_rom" "$app/user/pm.n64.us.z64"
      rm -rf "$state/.rom"
    fi
  '';

  # In-game saves travel; the ROM does not (it is refetchable and 22MB), and
  # neither do texture dumps or the early save-state format.
  saves = [ "recut/Paper Mario ReCut/user/**" ];
  saveExcludes = [
    "recut/Paper Mario ReCut/user/pm.n64.us.z64"
    "recut/Paper Mario ReCut/user/textures/dumps/**"
    "recut/Paper Mario ReCut/user/states/**"
  ];
}
