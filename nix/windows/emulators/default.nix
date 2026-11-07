# The emulators' official Windows builds, one file each, for the Windows facet
# (docs/windows.md): `windows.emulator = <this>.<emu>`.
{ callPackage }:
{
  dolphin = callPackage ./dolphin.nix { };
}
