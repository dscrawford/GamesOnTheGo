# The picker. A third component beside the service and the client, and
# deliberately not part of either: `src/gotg` ships inside the image that faces
# the internet and holds every credential — stdlib-only is that image's whole
# supply-chain posture — and the client is bash whose Python is helpers it
# shells out to. This is a program, and it depends on the client the way a
# person does: through the `gotg` command.
{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  resvg,
  mesa,
  gotg,
  padmap,
  # Everything under config/: the controller descriptions, the theme, the icon
  # rules. From the repository root rather than src/ui, because what a pad
  # covers and what colour player two is are not the picker's private business
  # -- they are things somebody is expected to edit.
  configDir ? ../../config,
}:

let
  # pygame-ce is SDL2, which is already the stack this repo reasons in:
  # gotg-pads is an SDL program and the controller bindings are written from
  # what SDL reports. A picker launched from Steam onto a handheld needs a
  # gamepad and a fullscreen window, which is the whole reason it is not tk.
  # pyyaml for config/controllers. The same choice the indexer made for
  # rules.yaml: a table somebody is expected to edit is worth a parser.
  python = python3.withPackages (ps: [
    ps.pygame-ce
    ps.pyyaml
  ]);
  # On NixOS, /run/opengl-driver carries the GPU userspace and pygame's SDL
  # just works. On a foreign distro -- SteamOS -- that path does not exist
  # and the host's mesa is unloadable from our glibc. Under Desktop Mode SDL
  # draws through X11 without GL and nobody notices; under Game Mode it is
  # Wayland, whose only path to a window surface is a GL renderer, and the
  # picker died with "Window framebuffer support not available". The same
  # inlined nixGL trick the emulator environments use, applied only when the
  # host provides nothing.
  foreignGl = ''
    if [ ! -e /run/opengl-driver ]; then
      export LIBGL_DRIVERS_PATH=${mesa}/lib/dri
      export __EGL_VENDOR_LIBRARY_FILENAMES=${mesa}/share/glvnd/egl_vendor.d/50_mesa.json
      export LD_LIBRARY_PATH=${mesa}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
    fi
  '';
in
stdenvNoCC.mkDerivation {
  pname = "gotg-ui";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  # resvg is a build-time tool and nothing more: it turns the controller SVGs
  # into the PNGs the diagram screen loads, and never enters the runtime
  # closure. pygame can rasterise an SVG itself, and deliberately is not asked
  # to — it clamps to the source aspect ratio, so a diagram sized from the
  # request rather than the result puts every leader line off its button.
  nativeBuildInputs = [
    makeWrapper
    resvg
    python3
  ];

  buildPhase = ''
    runHook preBuild
    python3 build-controllers.py assets/controllers assets/built
    # The strip's icons: a second pass, because these carry no anchors and
    # the diagram build refuses an SVG without them.
    python3 build-controllers.py --icons assets/icons assets/built/icons
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/gotg-ui
    cp -r gotg_ui $out/share/gotg-ui/
    cp -r assets/built $out/share/gotg-ui/assets
    cp -r ${configDir} $out/share/gotg-ui/config

    # The client's own artwork sources ride on PYTHONPATH rather than being
    # copied: the grid asks SteamGridDB and libretro-thumbnails exactly as
    # `gotg steam art` does, and a second implementation would be a second
    # thing to keep in step with an API neither of us controls.
    # GOTG_UI_ENV is the client's environment files, which is where a game's
    # variants are: a mod is a file there rather than a catalog row, so the
    # menu reads the same directory `gotg play <id> <variant>` resolves
    # against. GOTG_UI_DATA is the client's own table directory, which is where
    # ares-pads.json lives — the file `gotg pads` writes the bindings from. The
    # diagram reads that one rather than a copy, so what it draws and what the
    # emulator is given cannot disagree. GOTG_DATA wins when the client
    # exported it, which is the case for anything the client itself started.
    makeWrapper ${python}/bin/python3 $out/bin/gotg-ui \
      --add-flags "-m gotg_ui" \
      --run ${lib.escapeShellArg foreignGl} \
      --set PYTHONPATH "$out/share/gotg-ui:${gotg}/share/gotg/steam" \
      --set GOTG_UI_DATA "${gotg}/share/gotg/data" \
      --set GOTG_UI_ENV "${gotg}/share/gotg/env" \
      --set GOTG_UI_ASSETS "$out/share/gotg-ui/assets" \
      --set GOTG_CONFIG "$out/share/gotg-ui/config" \
      --prefix PATH : ${lib.makeBinPath [
        gotg
        padmap
      ]}

    # The launch-time controller check, as its own command. The client runs it
    # by name if it is on PATH and shrugs if it is not, which is what keeps the
    # dependency pointing one way: this package knows about the client, and the
    # client never has to know about this one.
    makeWrapper ${python}/bin/python3 $out/bin/gotg-seat \
      --add-flags "-m gotg_ui.seat" \
      --run ${lib.escapeShellArg foreignGl} \
      --set PYTHONPATH "$out/share/gotg-ui:${gotg}/share/gotg/steam" \
      --set GOTG_UI_DATA "${gotg}/share/gotg/data" \
      --set GOTG_UI_ENV "${gotg}/share/gotg/env" \
      --set GOTG_UI_ASSETS "$out/share/gotg-ui/assets" \
      --set GOTG_CONFIG "$out/share/gotg-ui/config" \
      --prefix PATH : ${lib.makeBinPath [ padmap ]}

    runHook postInstall
  '';

  meta = {
    description = "A grid to pick a game from, and play it";
    mainProgram = "gotg-ui";
    license = lib.licenses.mit;
  };
}
