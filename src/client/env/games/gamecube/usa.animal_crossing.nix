# Animal Crossing — the PC port built on ACreTeam's ac-decomp, which is a
# complete decompilation. The game's own C code compiled for x86, with a
# translation layer putting the GameCube's GX calls onto OpenGL 3.3. Not
# Dolphin, and not a recompilation either.
#
# Like Paper Mario ReCut next door it is **published for Windows only**, so
# this runs it under Wine. Unlike ReCut it is a 32-bit build — checked, not
# assumed: both PE files in the release are "PE32 ... Intel i386". That
# decides the Wine attribute. winePackages.stagingFull is a pure i386 build
# and is in the binary cache; wineWowPackages is not cached and would compile
# Wine from source for no gain, since there is no 64-bit half to serve.
#
# What it gives over emulating the disc: it is the game running natively, and
# upstream's own playtest notes fold in Cuyler36's Deluxe mod — outdoor
# camera control, D-pad tool shortcuts, shake trees while holding a net.
# Keybindings are a plain ini beside the executable and SDL2 pads hotplug.
#
# The disc is read whole at startup rather than extracted, which is the
# pleasant part: no 640MB unpacking step like Pikmin has, and nothing stale
# to invalidate. The cost is the format. The port reads ISO, GCM and CISO;
# gotg stores GameCube games as RVZ, so the first launch converts once with
# dolphin-tool — the same converter Pikmin uses, out of the dolphin-emu
# package this platform already has. That conversion is where the disc's real
# size shows up: a 17MB RVZ becomes about 1.4GB of ISO, which is why it lives
# in the game's state and is excluded from anything that syncs.
#
# Only the USA disc works: upstream supports GAFE01_00 Rev 0 and nothing
# else, which is the entry this file is named for.
{
  pkgs,
  lib,
  ...
}:
let
  version = "0.9.3";

  release = pkgs.fetchurl {
    url =
      "https://github.com/flyngmt/ACGC-PC-Port/releases/download/"
      + "v${version}-playtest/ACGC-PC-Port${version}.zip";
    hash = "sha256-T+EHYyOFltjx8nhGHPPz5Jx+mZA7n7ThJx6E5y5Hl2o=";
  };

  # i386 only. See the note at the top.
  wine = pkgs.winePackages.stagingFull;

  # And a 32-bit process needs a 32-bit GL stack. env/foreign-gl.nix points
  # the usual variables at nixpkgs' mesa when the host has none, which is the
  # SteamOS case -- but that mesa is x86-64, and handing a 32-bit libEGL a
  # 64-bit vendor library leaves it with no extensions at all:
  #
  #     err:wgl:egl_init Failed to find required extension
  #                      EGL_KHR_client_get_all_proc_addresses
  #
  # and the game draws nothing for as long as you let it run. These two are
  # the same libraries built for i686, and the launcher points the same
  # variables at them instead.
  mesa32 = pkgs.pkgsi686Linux.mesa;
  glvnd32 = pkgs.pkgsi686Linux.libglvnd;

  launcher = pkgs.writeShellApplication {
    name = "gotg-animal-crossing";
    runtimeInputs = [ wine ];
    text = ''
      state="''${1:?state directory required}"
      app="$state/acgc"

      export WINEPREFIX="$state/prefix"
      # Never the interactive ones: this is launched from Steam, where a
      # dialog nobody can see is indistinguishable from a hang.
      export WINEDLLOVERRIDES="mscoree,mshtml="
      export WINEDEBUG="-all"

      # The 32-bit GL stack. See the note in this file about the extension
      # a 64-bit vendor library cannot provide. NixOS keeps its own beside
      # the 64-bit one and that is the better answer where it exists, since
      # it is the machine's real driver rather than a generic mesa.
      if [ -e /run/opengl-driver-32 ]; then
        export LD_LIBRARY_PATH="/run/opengl-driver-32/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        export LIBGL_DRIVERS_PATH="/run/opengl-driver-32/lib/dri"
      else
        export LD_LIBRARY_PATH="${glvnd32}/lib:${mesa32}/lib"
        export LIBGL_DRIVERS_PATH="${mesa32}/lib/dri"
        export __EGL_VENDOR_LIBRARY_FILENAMES="${mesa32}/share/glvnd/egl_vendor.d/50_mesa.json"
      fi

      # It reads the disc, its shaders and its saves relative to here.
      cd "$app"
      exec wine AnimalCrossing.exe "$@"
    '';
  };
in
{
  emulator = launcher;
  bin = "gotg-animal-crossing";
  # Settings are the keybindings ini and the in-game menus; there is no
  # separate configuration program to open.
  configurable = false;
  isolate = true;
  args = [ "{state}" ];

  path = [
    pkgs.unzip
    pkgs.dolphin-emu
    wine
  ];

  preLaunch = ''
    app="$state/acgc"

    if [ ! -f "$app/AnimalCrossing.exe" ]; then
      echo "first run: unpacking the Animal Crossing PC port ${version}" >&2
      # Unpacked over rather than replaced. The obvious `rm -rf "$app"` first
      # is what ReCut does and it is wrong here, because rom/ lives inside
      # this directory: a QA run that had been handed a converted disc threw
      # it away and spent itself converting another one.
      mkdir -p "$app"
      unzip -q -o ${release} -d "$app"
      chmod -R u+w "$app"
    fi

    # The prefix is per-environment, so a wedged one is `rm -rf` on this
    # directory rather than anything shared.
    if [ ! -d "$state/prefix/drive_c" ]; then
      echo "first run: preparing the Wine prefix — a minute, once" >&2
      WINEPREFIX="$state/prefix" WINEDLLOVERRIDES="mscoree,mshtml=" WINEDEBUG=-all \
        wineboot --init >/dev/null 2>&1 || true
    fi

    # The disc, in the format the port can read. See the note at the top.
    if [ ! -f "$app/rom/animal_crossing.iso" ]; then
      echo "first run: converting the disc to ISO — about 1.4GB, once" >&2
      mkdir -p "$app/rom"
      # -u keeps dolphin-tool's scratch inside the game's state instead of in
      # the player's home, where a Dolphin configuration already lives.
      if ! dolphin-tool convert \
        -u "$state/.dolphin-tool" \
        -i "$target" \
        -o "$app/rom/animal_crossing.iso" \
        -f iso; then
        rm -f "$app/rom/animal_crossing.iso"
        echo "could not convert $target to an ISO the port can read" >&2
        exit 1
      fi
      rm -rf "$state/.dolphin-tool"
    fi
  '';

  # The two memory cards and the keybindings, which are a choice worth
  # keeping in step across machines.
  saves = [
    "acgc/save/**"
    "acgc/keybindings.ini"
  ];
  # The converted disc is 1.4GB that any machine can make again from the RVZ
  # it already has, and the NES roms inside the game are part of the disc,
  # not part of a save.
  saveExcludes = [
    "acgc/rom/**"
    "acgc/nes_roms/**"
    "acgc/texture_pack/**"
  ];
}
