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
# Both version files the archive ships are kept: Ryujinx matches a pchtxt by the
# build id inside it (@nsobid), so each is inert on the other's dump and the
# variant works whether or not the update is installed.
{ pkgs, lib }:
let
  fl4sh = import ./fl4sh.nix { inherit pkgs lib; };
  pack = fl4sh.fl4shPack {
    archive = "Kirby%20and%20the%20Forgotten%20Land%20%5B01004D300C5AE000%5D%5Bmods%5D.zip";
    hash = "sha256-TXPd8d3iWXDyIJgKCmDoJuxPUFP5P/rL2EGqTomr7CU=";
    name = "kirby-forgotten-land-mods";
  };
in
{
  # The archive's directory is "[60 FPS Static v1.1.0]".
  kirby60Mod = fl4sh.fl4shMod {
    name = "kirby-forgotten-land-60fps";
    inherit pack;
    folder = "[60 FPS Static v1.1.0]";
    checks = [ "test -f $out/exefs/1.1.0.pchtxt" ];
  };
}
