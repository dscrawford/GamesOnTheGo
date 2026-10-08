# Edit a JSON file in place with jq, and never leave it half written.
#
# This was eight hand-copied shells: Ryujinx's pinned settings, its pad
# restore and its Deck pass (switch.nix), the generic `configEdit`
# (ryujinx.nix), both UltraCams, the Harkinian settings (harkinian.nix) and
# the co-op copy's Anchor block (oot-coop-split.nix). They agreed on the part
# that matters -- jq writes a sibling, and only a jq that succeeded replaces
# the original, so a filter that errors (or a disk that fills) cannot truncate
# a config the emulator would then reject -- and had drifted on the rest: one
# kept the temp file when jq failed, one used `mv -f`, three spelled the temp
# name differently. One place says it now; what is left as a parameter is what
# really differed.
#
#   file      shell text for the path, written between double quotes
#             (`$config`, `$ryujinx/Config.json`)
#   filter    the jq program, written between single quotes; or
#   filterVar the name of a shell variable that holds it (the UltraCams pick
#             their program by machine at launch)
#   args      jq options placed before the program (`--argjson fs "$x"`)
#   suffix    the sibling's extension
#   jq        the binary: the store's by default, `jq` for a launcher that
#             already has it on PATH (the Harkinian ports)
#   force     `mv -f`
#   indent    spaces to put before every line after the first, so the snippet
#             sits at the depth of the line it is interpolated into (Nix only
#             indents what is written in the file, not what is spliced in)
{ pkgs }:
let
  inherit (pkgs) lib;
in
{
  gotgJqEdit =
    {
      file,
      filter ? null,
      filterVar ? null,
      args ? null,
      suffix ? ".gotg",
      jq ? "${pkgs.jq}/bin/jq",
      force ? false,
      indent ? "",
    }:
    assert (filter == null) != (filterVar == null);
    let
      tmp = ''"${file}${suffix}"'';
      program = if filter != null then "'${filter}'" else ''"${"$"}${filterVar}"'';
      options = if args == null then "" else "${args} ";
    in
    lib.replaceStrings [ "\n" ] [ "\n${indent}" ] ''
      if ${jq} ${options}${program} "${file}" >${tmp}; then
        mv ${if force then "-f " else ""}${tmp} "${file}"
      else
        rm -f ${tmp}
      fi'';
}
