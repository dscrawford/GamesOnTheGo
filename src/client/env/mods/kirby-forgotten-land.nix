# Kirby and the Forgotten Land's frame-rate patch.
#
# The game ships locked to 30, and Fl4sh9174's archive offers one way off that:
# a *static* 60. The distinction is the author's own — "the 60 FPS mod included
# is the static version, and if you unlock the framerate, game will speed up.
# The Dynamic mod is almost ready, but no ETA." Static means the patch presents
# a frame per vblank and the game's logic still assumes a fixed rate, so the
# emulated display has to stay at 60Hz; raise it and the game runs fast rather
# than smooth. That is why there is a 60fps variant here and no 120fps one.
#
# Both version files the archive ships are kept. Ryujinx matches a pchtxt to
# the running executable by the build id inside it (@nsobid), so the 1.0.0 file
# is inert on a 1.1.0 dump and vice versa, and shipping both means the variant
# works whether or not the update is installed.
{ pkgs, lib }:
let
  rev = "a398d55b625129365a975c3ab0b9ef25012d5fff";
  mods = pkgs.fetchzip {
    url =
      "https://raw.githubusercontent.com/Fl4sh9174/Switch-Emulator-Ultrawide-FPS-Mods/"
      + "${rev}/Kirby%20and%20the%20Forgotten%20Land%20%5B01004D300C5AE000%5D%5Bmods%5D.zip";
    hash = "sha256-TXPd8d3iWXDyIJgKCmDoJuxPUFP5P/rL2EGqTomr7CU=";
    # One directory per mod at the top of the archive, so there is no single
    # root to strip.
    stripRoot = false;
    name = "kirby-forgotten-land-mods";
  };
in
{
  # Copied out to a name of our own rather than referenced where it lands: the
  # archive's directories are "[60 FPS Static v1.1.0]", and a store path with a
  # space and a bracket in it is one that has to be quoted correctly by every
  # line that ever touches it.
  kirby60Mod = pkgs.runCommand "kirby-forgotten-land-60fps" { } ''
    cp -R --no-preserve=mode ${lib.escapeShellArg "${mods}/[60 FPS Static v1.1.0]"} $out
    test -f $out/exefs/1.1.0.pchtxt
  '';
}
