# Switch — Ryubing, the maintained Ryujinx fork, as nixpkgs dropped the
# original.
{ pkgs, ... }:
{
  emulator = pkgs.ryubing;
  bin = "Ryujinx";
  args = [ "{target}" ];
}
