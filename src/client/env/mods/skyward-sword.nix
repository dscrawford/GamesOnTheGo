# Skyward Sword HD's frame-rate patch.
#
# The game already runs at 60, which is the whole reason there is one variant
# here rather than two: 120 is the only thing left to ask for. The patch is
# Fl4sh9174's, from a repository of pchtxt mods for Switch emulators, and it
# ships with a warning from its author — "this mod is experimental, had no time
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
  # Both 120 FPS files, not a pick between them. They are not a patch and a
  # revision of it: "1.0.1.pchtxt" and "1.0.1v2.pchtxt" carry different build
  # ids for the same 1.0.1, so they are two dumps, and Ryujinx applies whichever
  # one matches the executable actually loaded. Shipping one was choosing which
  # dump the variant works on, by hand, without knowing which one is here.
  #
  # Copied out to a name of our own rather than referenced where it lands: the
  # archive's directories are "[120FPS v1.0.1]", and a store path with a space
  # and a bracket in it is one that has to be quoted correctly by every line
  # that ever touches it.
  skywardSword120Mod = pkgs.runCommand "skyward-sword-hd-120fps" { } ''
    cp -R --no-preserve=mode ${lib.escapeShellArg "${mods}/[120FPS v1.0.1]"} $out
    test -f $out/exefs/1.0.1.pchtxt
    test -f $out/exefs/1.0.1v2.pchtxt
  '';
}
