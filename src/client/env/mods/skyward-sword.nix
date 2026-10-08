# Skyward Sword HD's frame-rate patch.
#
# The game already runs at 60, which is the whole reason there is one variant
# here rather than two: 120 is the only thing left to ask for. The patch is
# Fl4sh9174's pchtxt pack, and it ships with a warning from its author — "this mod is experimental, had no time
# to test it enough" — which is repeated here because a frame-rate patch on a
# motion-controlled game is exactly where an untested one shows.
#
# Only v1.0.1 is patched, and that is the whole reason the variant states it.
# Ryujinx matches a pchtxt to the running executable by the build id inside the
# file (@nsobid), so on a 1.0.0 dump nothing is applied and the game runs as it
# shipped — no error, no 120, nothing to see. A headless run proved it: the mod
# loaded, VSync went to 120, and not one patch line matched. So the variant
# declares 1.0.1 at both ends and the client disables it rather than letting it
# look like it worked.
{ pkgs, lib }:
let
  fl4sh = import ./fl4sh.nix { inherit pkgs lib; };
  pack = fl4sh.fl4shPack {
    archive = "The%20Legend%20of%20Zelda%20Skyward%20Sword%20HD%20%5B01002DA013484000%5D%5Bmods%5D.zip";
    hash = "sha256-buVMskOa2cQ7cqjUBjVjCCcILwRSUFEyUDLFC14ZyU4=";
    name = "skyward-sword-hd-mods";
  };
in
{
  # Both 120 FPS files, not a pick between them: "1.0.1.pchtxt" and
  # "1.0.1v2.pchtxt" carry different build ids for the same 1.0.1 (see
  # fl4sh.nix), so they are two dumps, and the checks name both.
  skywardSword120Mod = fl4sh.fl4shMod {
    name = "skyward-sword-hd-120fps";
    inherit pack;
    folder = "[120FPS v1.0.1]";
    checks = [
      "test -f $out/exefs/1.0.1.pchtxt"
      "test -f $out/exefs/1.0.1v2.pchtxt"
    ];
  };
}
