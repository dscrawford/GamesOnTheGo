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
  mesa,
  gotg,
  gotg-killswitch,
  danstick,
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
  # GL on a machine that is not NixOS. Under Desktop Mode SDL draws through
  # X11 without GL and nobody notices; under Game Mode it is Wayland, whose
  # only path to a window surface is a GL renderer, and the picker died with
  # "Window framebuffer support not available". The same block the emulator
  # environments and the QA tools use.
  foreignGlParts = import ../client/env/foreign-gl.nix { inherit mesa; };
  foreignGl = foreignGlParts.guarded;
in
stdenvNoCC.mkDerivation {
  pname = "gotg-ui";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ makeWrapper ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/gotg-ui
    # Named, not carried: `gotg sync` fetches it where it is loaded.
    echo ${foreignGlParts.path} >$out/share/gotg-ui/foreign-gl
    cp -r gotg_ui $out/share/gotg-ui/
    cp -r ${configDir} $out/share/gotg-ui/config

    # The client's own artwork sources ride on PYTHONPATH rather than being
    # copied: the grid asks SteamGridDB and libretro-thumbnails exactly as
    # `gotg steam art` does, and a second implementation would be a second
    # thing to keep in step with an API neither of us controls.
    # GOTG_UI_ENV is the client's environment files, which is where a game's
    # variants are: a mod is a file there rather than a catalog row, so the
    # menu reads the same directory `gotg play <id> <variant>` resolves
    # against.
    makeWrapper ${python}/bin/python3 $out/bin/gotg-ui \
      --add-flags "-m gotg_ui" \
      --run ${lib.escapeShellArg foreignGl} \
      --set PYTHONPATH "$out/share/gotg-ui:${gotg}/share/gotg/steam" \
      --set GOTG_UI_ENV "${gotg}/share/gotg/env" \
      --set GOTG_CONFIG "$out/share/gotg-ui/config" \
      --set GOTG_UI_SELF "$out" \
      --prefix PATH : ${lib.makeBinPath [
        gotg
        danstick
        # The bar over the picker, as over a game: gotg_ui/beside.py.
        gotg-killswitch
      ]}

    runHook postInstall
  '';

  meta = {
    description = "A grid to pick a game from, and play it";
    mainProgram = "gotg-ui";
    license = lib.licenses.mit;
  };
}
