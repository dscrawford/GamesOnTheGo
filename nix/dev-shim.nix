# A dev-shell command that runs the working tree, not the store. Its own file
# because the shell has two of them (`gotg`, the picker) and each was the same
# lookup of the checkout, the same refusal when it is not one, and the same
# advice about the two ways out -- and the third would have been a copy of
# those eight lines again.
{ pkgs }:
{
  # The command's name (also what its refusal says).
  name,
  # A file or directory that makes a directory this checkout, relative to it,
  # and the `test` flag that finds it: -x for a script, -d for a package.
  marker,
  test,
  # The flake output that is the packaged article, for the refusal to name.
  packaged,
  # What the shim does once it has `$root`: bash.
  body,
}:
pkgs.writeShellScriptBin name ''
  root="''${GOTG_DEV_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
  if [ ! ${test} "$root/${marker}" ]; then
    echo "${name}: no ${marker} under $root" >&2
    echo "      set GOTG_DEV_ROOT to your checkout, or use: nix run .#${packaged}" >&2
    exit 1
  fi
  ${pkgs.lib.removeSuffix "\n" body}
''
