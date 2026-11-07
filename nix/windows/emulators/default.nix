# Every emulator or port's Windows build, by name: what a Windows facet's
# `emulator` points at (docs/windows.md). One line per file beside this one;
# each is exposed as the flake package windows-<name> (nix/packages.nix).
{ pkgs }:
let
  inherit (pkgs) callPackage;
in
{
  ares = callPackage ./ares.nix { };
  dolphin = callPackage ./dolphin.nix { };
}
