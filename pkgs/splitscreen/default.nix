# SplitScreenWrapper — several games, or several windows of one game, inside a
# single window that the desktop treats as one.
#
# Pulled in by the games that need it and by nothing else: the client does not
# carry it, so a machine that never launches a split-screen variant never
# builds sway, gamescope and bubblewrap for the privilege. Four Swords
# Adventures is the first — one Dolphin, one window per player's Game Boy
# Advance, laid out around the television screen.
#
# Not packaged upstream, and it is ours, so it is fetched by revision rather
# than by a tag that could move under a game that was working yesterday.
{
  lib,
  stdenvNoCC,
  fetchFromGitHub,
  makeWrapper,
  python3,
  sway,
  gamescope,
  bubblewrap,
  xorg,
}:

let
  # i3ipc talks to the nested sway; evdev is the keyboard-to-gamepad path, which
  # session.py reaches through kbd2pad. pygame is the layout editor's, and the
  # editor is not something a game launch has any use for.
  python = python3.withPackages (ps: [
    ps.i3ipc
    ps.evdev
  ]);
in
stdenvNoCC.mkDerivation {
  pname = "splitscreen";
  version = "0-unstable-2026-09-17";

  src = fetchFromGitHub {
    owner = "dscrawford";
    repo = "SplitScreenWrapper";
    rev = "428a48d20c301ae27568144d86bd700ea5fd8402";
    hash = "sha256-VPR52+2NRmzPFw4teOX0UKHjbQi4gbFWX/kzLHgqjVU=";
  };

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/splitscreen
    cp -r splitscreen $out/share/splitscreen/

    # python3 on PATH as well as as the entry point: the session runs a config's
    # `pre_launch` commands as it finds them, and the one that binds controllers
    # to Dolphin's GBA ports is `python3 -m splitscreen.handlers.dolphin_gba`.
    for entry in session modes.fsa; do
      makeWrapper ${lib.getExe python} $out/bin/splitscreen-''${entry#modes.} \
        --add-flags "-m splitscreen.$entry" \
        --set PYTHONPATH $out/share/splitscreen \
        --prefix PATH : ${
          lib.makeBinPath [
            python
            sway
            gamescope
            bubblewrap
            xorg.xrandr # the frame asks the screen how big it is
          ]
        }
    done

    runHook postInstall
  '';

  meta = {
    description = "Puts several game windows into one window, with a layout and per-player input";
    homepage = "https://github.com/dscrawford/SplitScreenWrapper";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
  };
}
