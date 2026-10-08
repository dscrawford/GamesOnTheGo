# Fl4sh9174's Switch frame-rate packs, the way every one of them is taken.
#
# Kirby, Super Mario RPG, Skyward Sword HD and Paper Mario each fetched the
# same repository at the same revision and then copied one folder out of the
# archive under a name of their own, four times, each with its own account of
# why. The account is here once.
#
# The archive is one zip per game, with one directory per mod at its top, so
# there is no single root to strip (stripRoot = false).
#
# Copied out to a name of our own rather than referenced where it lands: the
# directories are called "[120FPS v1.0.1]", "[60 FPS Static v1.1.0]",
# "[120FPS v1.0.1]WIP" -- brackets, spaces and all -- and a store path with a
# space and a bracket in it is one that has to be quoted correctly by every
# line that ever touches it. Quoted once here, it is an ordinary path after.
#
# Every version file the archive ships is kept, not a pick between them. They
# are not a patch and a revision of it: the pchtxt files for one game version
# can carry different build ids (@nsobid) for the same release, which are two
# dumps of it, and Ryujinx applies whichever one matches the executable it
# loaded. A pchtxt for another version is inert, so shipping all of them makes
# the variant work whichever dump is installed -- and keeps working when the
# game is updated. Shipping one was choosing, by hand and without knowing
# which was here. `checks` names the files the folder must hold, so a bump of
# the pin that renames one fails the build rather than the launch.
{ pkgs, lib }:
let
  rev = "a398d55b625129365a975c3ab0b9ef25012d5fff";
in
{
  # One game's archive, unpacked. `archive` is the zip's path in the
  # repository, already percent-encoded (it has brackets and spaces).
  fl4shPack =
    {
      archive,
      hash,
      name,
    }:
    pkgs.fetchzip {
      url =
        "https://raw.githubusercontent.com/Fl4sh9174/Switch-Emulator-Ultrawide-FPS-Mods/"
        + "${rev}/${archive}";
      inherit hash name;
      stripRoot = false;
    };

  # That folder, copied out under `name`, and checked to hold what the caller
  # leans on (`checks` are shell lines, each a `test`). `folder` is the
  # directory's name exactly as the archive spells it, brackets included --
  # "[120FPS v1.0.1]WIP" has text after them, so they cannot be added here.
  fl4shMod =
    {
      name,
      pack,
      folder,
      checks,
    }:
    pkgs.runCommand name { } ''
      cp -R --no-preserve=mode ${lib.escapeShellArg "${pack}/${folder}"} $out
      ${lib.concatStringsSep "\n" checks}
    '';
}
