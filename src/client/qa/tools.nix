# The tools a QA session needs and the client's own runtime does not carry:
# a compositor with no monitor, recorders, analyzers, and a python that can
# create uinput devices. Built on demand by `gotg qa` the way emulator
# environments are, so the client package stays the size it is.
{
  pkgs,
  lib,
}:
pkgs.buildEnv {
  name = "gotg-qa-tools";
  paths = with pkgs; [
    # xwayland passed explicitly: ares and dolphin render through X11 paths on
    # some setups, and a kiosk that cannot host them fails as "no window", which
    # reads as the game's fault.
    (cage.override { xwayland = pkgs.xwayland; })
    wf-recorder
    ffmpeg
    imagemagick
    pulseaudio # pactl, against the host's pipewire-pulse
    # glxinfo and eglinfo: what a client inside the session actually gets for
    # GL, which the emulator's own log only says when it succeeds.
    mesa-demos
    # Under its own name, not python3: the client carries a python3 of its own
    # (for vdf), and whichever lands first on PATH would otherwise decide
    # whether the virtual pad can be created at all. In the image the client's
    # wins, and pad.py dies on `import evdev`.
    (writeShellScriptBin "gotg-qa-python" ''
      exec ${python3.withPackages (ps: [ ps.evdev ])}/bin/python3 "$@"
    '')
  ];
  meta.description = "Compositor, recorders and analyzers for gotg qa";
}
