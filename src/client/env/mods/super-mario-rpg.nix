# Super Mario RPG's frame-rate patch.
#
# The remake already runs at 60, which is why there is one variant rather than
# two: 120 is the only thing left to ask for. Fl4sh9174 marks that mod WIP in
# the archive's own directory name, and it is passed through with the label
# intact — this is the variant to suspect first if something misbehaves.
#
# The two files are not a patch and a revision of it but two different dumps:
# they carry different build ids (@nsobid) for the same 1.0.1, so Ryujinx
# applies whichever one matches the executable actually loaded and ignores the
# other. Shipping both is what makes the variant work regardless of which dump
# is installed.
{ pkgs, lib }:
let
  rev = "a398d55b625129365a975c3ab0b9ef25012d5fff";
  mods = pkgs.fetchzip {
    url =
      "https://raw.githubusercontent.com/Fl4sh9174/Switch-Emulator-Ultrawide-FPS-Mods/"
      + "${rev}/Super%20Mario%20RPG%20%5B0100BC0018138000%5D%5Bmods%5D.zip";
    hash = "sha256-up3i4q8bSMJEY4TtGi9/lzsGeFNVqD5/jX8l/18V5qI=";
    stripRoot = false;
    name = "super-mario-rpg-mods";
  };
in
{
  # Copied out to a name of our own: the archive's directory is
  # "[120FPS v1.0.1]WIP", brackets, space and all.
  superMarioRpg120Mod = pkgs.runCommand "super-mario-rpg-120fps" { } ''
    cp -R --no-preserve=mode ${lib.escapeShellArg "${mods}/[120FPS v1.0.1]WIP"} $out
    test -f $out/exefs/1.0.1.pchtxt
  '';
}
