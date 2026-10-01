# gotg.lib: what a library flake builds games with. See docs/nix-games.md.
{ lib }:
{
  catalog = import ./catalog.nix { inherit lib; };
}
