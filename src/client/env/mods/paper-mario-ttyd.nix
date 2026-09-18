# Paper Mario: The Thousand-Year Door's frame-rate patch.
#
# The remake runs at 30. Fl4sh9174's 60 FPS mod is three things at once and
# needs all three: an executable patch, one replaced game file
# (romfs/data/battle/weapon — battle timing reads it), and a cheat.
#
# The cheat is not an extra. At 60 the author documents two places where the
# game's own logic comes apart — Punie's AI can stop following in Chapter 2, and
# the Pianta Parlor paper game runs at double speed — and the fix for both is to
# drop back to 30 for that stretch: ZL + D-pad Down for 30, ZL + D-pad Up for
# 60. Installing the patch without it would leave a player stuck in a minigame
# with no way out, so it ships switched on.
#
# Both version files are kept. Ryujinx matches a pchtxt to the executable by
# the build id inside it (@nsobid), and the cheat files are *named* for those
# build ids, so the 1.0.0 and 1.0.1 halves each apply to their own dump and lie
# inert on the other.
{ pkgs, lib }:
let
  rev = "a398d55b625129365a975c3ab0b9ef25012d5fff";
  mods = pkgs.fetchzip {
    url =
      "https://raw.githubusercontent.com/Fl4sh9174/Switch-Emulator-Ultrawide-FPS-Mods/"
      + "${rev}/Paper%20Mario%20The%20Thousand-Year%20Door%20%5B0100ECD018EBE000%5D%5Bmods%5D.zip";
    hash = "sha256-pGNfImmUxXdVu8F0Wdf6GiuAk5vfqgUxOjmF6mNeiQY=";
    stripRoot = false;
    name = "paper-mario-ttyd-mods";
  };
  mod = "${mods}/[60FPS v1.0.1]";
in
{
  # Copied out to a name of our own: the archive's directory is
  # "[60FPS v1.0.1]", and a store path with a space and a bracket in it is one
  # that has to be quoted correctly by every line that ever touches it.
  paperMarioTtyd60Mod = pkgs.runCommand "paper-mario-ttyd-60fps" { } ''
    cp -R --no-preserve=mode ${lib.escapeShellArg mod} $out
    test -f $out/exefs/1.0.1.pchtxt
    test -f $out/romfs/data/battle/weapon/data_battle_weapon_party.elf.zst
  '';

  # Any other folder out of the same archive, by name.
  #
  # The download is the author's whole pack for this game, and the 60 FPS
  # patch is one folder of about twenty. The rest are in-engine resolutions
  # from 720p to 8K, the lighting fix the ones above 1080p need, level of
  # detail, and switches for the game's own sharpening and colour filters.
  # Copied out to a name without spaces or brackets in it, for the same reason
  # the 60 FPS one is.
  paperMarioTtydMod =
    folder:
    pkgs.runCommand "paper-mario-ttyd-${lib.replaceStrings [ " " "." ] [ "-" "-" ] folder}" { }
      ''
        cp -R --no-preserve=mode ${lib.escapeShellArg "${mods}/[${folder}]"} $out
        test -d $out/exefs
      '';

  # Which cheats start switched on, in the one format Ryujinx reads them in:
  # "<build id>-<<cheat name> Cheat>", one per line, where the build id is the
  # cheat file's name and the cheat name is a bracketed section heading inside
  # it. A heading with no instructions under it is not a cheat — that is how
  # Ryujinx parses the file, and "[made by Fl4sh]" at the foot of each one is
  # exactly that case.
  #
  # Read back out of the archive rather than written down here, so that a later
  # revision that renames a cheat cannot leave this list quietly pointing at a
  # name that no longer exists.
  paperMarioTtyd60Cheats =
    pkgs.runCommand "paper-mario-ttyd-60fps-enabled-cheats" { }
      ''
        for cheats in ${lib.escapeShellArg mod}/cheats/*.txt; do
          id="$(basename "$cheats" .txt | tr '[:lower:]' '[:upper:]')"
          ${pkgs.gawk}/bin/awk -v id="$id" '
            # The archive keeps DOS line endings, and Ryujinx reads the file a
            # line at a time — so the carriage return is not part of any name.
            { sub(/\r$/, "") }
            /^\[/ {
              if (name != "" && lines > 0) print id "-<" name " Cheat>"
              name = substr($0, 2, length($0) - 2)
              lines = 0
              next
            }
            NF { lines++ }
            END { if (name != "" && lines > 0) print id "-<" name " Cheat>" }
          ' "$cheats"
        done >$out

        # Every line a build id and a cheat name, and no bracket left in the
        # name: the section headings are bracketed and the file has DOS line
        # endings, so getting one character out here is how a name that Ryujinx
        # will never match gets written silently.
        test -s $out
        test "$(${pkgs.gnugrep}/bin/grep -cE '^[0-9A-F]{16}-<[^][]+ Cheat>$' $out)" \
          = "$(wc -l <$out)"
      '';
}
