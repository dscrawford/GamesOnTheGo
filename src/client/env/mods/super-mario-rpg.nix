# Super Mario RPG's frame-rate patch.
#
# The remake already runs at 60, which is why there is one variant rather than
# two: 120 is the only thing left to ask for. Fl4sh9174 marks that mod WIP in
# the archive's own directory name, and it is passed through with the label
# intact — this is the variant to suspect first if something misbehaves.
#
# The two files are two different dumps of 1.0.1 (different @nsobid), not a patch
# and its revision; shipping both makes the variant work whichever is installed.
# See fl4sh.nix, and skyward-sword.nix, which has the same pair.
{ pkgs, lib }:
let
  fl4sh = import ./fl4sh.nix { inherit pkgs lib; };
  pack = fl4sh.fl4shPack {
    archive = "Super%20Mario%20RPG%20%5B0100BC0018138000%5D%5Bmods%5D.zip";
    hash = "sha256-up3i4q8bSMJEY4TtGi9/lzsGeFNVqD5/jX8l/18V5qI=";
    name = "super-mario-rpg-mods";
  };
in
{
  # The archive's directory is "[120FPS v1.0.1]WIP".
  superMarioRpg120Mod = fl4sh.fl4shMod {
    name = "super-mario-rpg-120fps";
    inherit pack;
    folder = "[120FPS v1.0.1]WIP";
    checks = [ "test -f $out/exefs/1.0.1.pchtxt" ];
  };
}
