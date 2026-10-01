# The two SDL3s GOTG's crates link, cut to what each one does.
#
# The stock build is ~940MiB of closure: zenity (gtk4, gstreamer, flite) for
# message boxes, pipewire, jack and pulse for sound, libdecor, ibus, a tray.
# gotg-pads asks one question -- which controllers are there, and what are
# their GUIDs -- and the overlay draws over a game and reads pads. Neither
# makes a sound or shows a dialog.
#
#   gamepad  gotg-pads: the joystick subsystem and nothing else. The same
#            switches danstick's sdl3Gamepad uses (danstick flake.nix), so
#            the GUIDs it reports are the ones danstick's env.sh says, built
#            the same way -- which is what pads_seating matches them by.
#   overlay  gotg-killswitch: video (Wayland for layer-shell, X11 for cage
#            and gamescope, GL and Vulkan for the renderer) and pads; no
#            audio, ibus, tray or dbus. nixpkgs ties zenity to
#            waylandSupport -- the path is substituted into SDL's message box
#            code -- so it gets a stand-in that only fails: the overlay calls
#            no message box, and a dialog over a game is not something it
#            should ever show.
#
# libusb stays in both. Without it SDL's HIDAPI saw no Steam Controller at
# all -- a pad SDL reaches through /dev/hidraw, with no joystick node for any
# other way in -- and gotg-pads answered `[]` on a desk with one plugged in.
# danstick's sdl3Gamepad switches it off, which suits a probe of the
# database; a list of the pads in the room is another question.
#
# SDL's own suite is off for both: it inits the subsystems switched off here.
{ sdl3, writeShellScriptBin }:
let
  quiet = {
    alsaSupport = false;
    dbusSupport = false;
    ibusSupport = false;
    jackSupport = false;
    pipewireSupport = false;
    pulseaudioSupport = false;
    traySupport = false;
  };
  noCheck = drv: drv.overrideAttrs { doCheck = false; };
in
{
  gamepad = noCheck (
    sdl3.override (
      quiet
      // {
        drmSupport = false;
        libdecorSupport = false;
        openglSupport = false;
        vulkanSupport = false;
        waylandSupport = false;
        x11Support = false;
      }
    )
  );

  overlay = noCheck (
    sdl3.override (
      quiet
      // {
        libdecorSupport = false;
        zenity = writeShellScriptBin "zenity" "exit 1";
      }
    )
  );
}
