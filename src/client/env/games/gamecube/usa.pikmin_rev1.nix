# Pikmin — Open Nectar, the native port built on the projectPiki
# decompilation. Not Dolphin: the game's own code compiled for x86-64 with GX
# translated to OpenGL, so 30, 60 or 120 fps and a HUD laid out for 16:9
# rather than stretched to it.
#
# The port installs before it plays: a launcher converts the disc image,
# extracts about 640 MB of game data, and only then is there anything to run.
# It takes --rom/--install-dir/--extract-only and states that this works with
# no desktop session, which is what lets the whole thing happen unattended
# here instead of behind a wizard.
#
# Three things this needed, all found by doing it:
#
#   * **The disc image must be verified past, not verified.** gotg's GameCube
#     dumps are NKit-processed, and converting one back to an ISO cannot
#     reproduce a raw dump byte for byte, so the launcher's integrity check
#     rejects it as damaged:
#
#         This image does not match an intact dump of Pikmin USA Rev. 1.
#
#     --skip-verify is the documented way past, and the check it skips is one
#     gotg already does: `gotg install` verified this file's checksum against
#     the catalog before it landed on disk. With it skipped the extraction
#     runs to completion and every asset comes out.
#
#   * **RVZ needs Dolphin's converter.** The port reads ISO and GCM itself
#     and shells out for anything compressed. dolphin-tool ships in the same
#     dolphin-emu package this platform already uses for emulation, so it
#     costs nothing to put on PATH.
#
#   * **The last step of the install fails, and that is fine.** Having
#     extracted the data, the launcher copies its own binaries next to it and
#     cannot, because they are in the store: "Could not copy the lib folder:
#     Permission denied". Nothing downstream wants that copy — the game runs
#     from the store and only reads assets/ — so what is checked is whether
#     the data arrived, not what the launcher returned.
#
# The game reads its data relative to the working directory, so the launch
# happens in the directory the data went into. Saves land there too, as
# ordinary files.
{
  pkgs,
  lib,
  helpers,
  gotgPkgs,
  ...
}:
let
  port = gotgPkgs.open-nectar;
in
{
  emulator = port;
  bin = "nectar";
  # Not the platform emulator: a native port. `gotg play <id> emulate`
  # is the way back to ares/dolphin when this one misbehaves.
  nativePort = true;
  # Settings are in-game, on F1. There is no separate configuration screen to
  # open, and `gotg configure` opening it would just start the game.
  configurable = false;
  isolate = true;
  # The disc image is not an argument: the assets were extracted from it
  # already, and the game reads those.
  args = [ ];

  # dolphin-tool, for the RVZ conversion. See the note at the top.
  path = [ pkgs.dolphin-emu ];

  preLaunch = ''
    run="$state/nectar"
    mkdir -p "$run"

    if [ ! -d "$run/assets" ]; then
      echo "first run: extracting game data from $target" >&2
      echo "  about 640 MB, a few minutes, and only this once" >&2
      # Failure is judged by what landed, not by what it returned. See the
      # note at the top about the lib folder.
      ${port}/bin/nectar-launcher \
        --rom "$target" \
        --install-dir "$run" \
        --extract-only \
        --skip-verify \
        --dolphin-tool "$(command -v dolphin-tool)" || true

      if [ ! -d "$run/assets" ]; then
        echo "could not extract the game data from $target" >&2
        exit 1
      fi
    fi

    # X11 for the window, EGL for the context. Both halves were learned the
    # hard way under the QA compositor:
    #
    #   * left alone it takes X11 and asks for a GLX visual that Xwayland on
    #     NVIDIA does not have -- "SDL_CreateWindow failed: Couldn't find
    #     matching GLX visual", and its own retry "without NVIDIA EGL/GLX
    #     pins" fails identically. Asking for EGL instead sidesteps the
    #     visual hunt entirely.
    #
    #   * pointed at Wayland it gets further and then simply stops, with no
    #     error after "Initializing SDL2 window and GL context...", on the
    #     Deck as well as here.
    #
    # Under gamescope in Game Mode this is the well-trodden path anyway:
    # nearly everything it runs is an X11 client.
    export SDL_VIDEODRIVER=x11
    export SDL_VIDEO_X11_FORCE_EGL=1

    # It resolves assets/ relative to here, and writes saves here too.
    cd "$run"
  '';

  # Saves are ordinary files in a memory-card directory, and the settings
  # file beside them is small and worth keeping in step across machines.
  saves = [
    "nectar/save/**"
    "nectar/pikmin_settings.conf"
  ];
  # The extracted data is 640 MB that any machine can make again from the
  # disc image it already has, and the shader cache is per-GPU by
  # definition — neither is worth carrying.
  saveExcludes = [
    "nectar/assets/**"
    "nectar/*.real"
    "nectar/lib/**"
    "nectar/save/shaders/**"
    "nectar/**/*.shadercache"
  ];
}
