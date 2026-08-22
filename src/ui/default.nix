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
  gotg,
}:

let
  # pygame-ce is SDL2, which is already the stack this repo reasons in:
  # gotg-pads is an SDL program and the controller bindings are written from
  # what SDL reports. A picker launched from Steam onto a handheld needs a
  # gamepad and a fullscreen window, which is the whole reason it is not tk.
  python = python3.withPackages (ps: [ ps.pygame-ce ]);
in
stdenvNoCC.mkDerivation {
  pname = "gotg-ui";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/gotg-ui
    cp -r gotg_ui $out/share/gotg-ui/

    makeWrapper ${python}/bin/python3 $out/bin/gotg-ui \
      --add-flags "-m gotg_ui" \
      --set PYTHONPATH "$out/share/gotg-ui" \
      --prefix PATH : ${lib.makeBinPath [ gotg ]}

    runHook postInstall
  '';

  meta = {
    description = "A grid to pick a game from, and play it";
    mainProgram = "gotg-ui";
  };
}
