# Skyward Sword HD's frame-rate patch.
#
# The game already runs at 60, which is the whole reason there is one variant
# here rather than two: 120 is the only thing left to ask for. The patch is
# Fl4sh9174's, from a repository of pchtxt mods for Switch emulators, and it
# ships with a warning from its author — "this mod is experimental, had no time
# to test it enough" — which is repeated here because a frame-rate patch on a
# motion-controlled game is exactly where an untested one shows.
#
# Only v1.0.1 is patched. Ryujinx matches a pchtxt to the running executable by
# the build id inside the file (@nsobid), so on a 1.0.0 dump nothing is applied
# and the game runs as it shipped — no error, no 120, which is the failure to
# expect if this variant seems to do nothing.
{ pkgs, lib }:
let
  rev = "a398d55b625129365a975c3ab0b9ef25012d5fff";
  mods = pkgs.fetchzip {
    url =
      "https://raw.githubusercontent.com/Fl4sh9174/Switch-Emulator-Ultrawide-FPS-Mods/"
      + "${rev}/The%20Legend%20of%20Zelda%20Skyward%20Sword%20HD%20%5B01002DA013484000%5D%5Bmods%5D.zip";
    hash = "sha256-buVMskOa2cQ7cqjUBjVjCCcILwRSUFEyUDLFC14ZyU4=";
    # Several directories at the top of the archive, one per mod, so there is
    # no single root to strip.
    stripRoot = false;
    name = "skyward-sword-hd-mods";
  };
in
{
  # The first of the two 120 FPS files. The sibling "1.0.1v2.pchtxt" is a
  # shorter rewrite of the same idea; this is the one the archive names for the
  # version, and picking by name rather than by "the newest looking" keeps what
  # is installed something a person can check against the archive.
  #
  # Copied out to a name of our own rather than referenced where it lands: the
  # archive's directories are "[120FPS v1.0.1]", and a store path with a space
  # and a bracket in it is one that has to be quoted correctly by every line
  # that ever touches it.
  skywardSword120Patch =
    pkgs.runCommand "skyward-sword-hd-120fps.pchtxt" { }
      ''cp ${lib.escapeShellArg "${mods}/[120FPS v1.0.1]/exefs/1.0.1.pchtxt"} $out'';
}
