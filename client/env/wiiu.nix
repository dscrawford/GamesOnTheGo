# Wii U — cemu. It wants the .rpx inside a decrypted title rather than the
# directory itself, which is what the `target` glob in data/overrides.json picks
# out before the ROM ever reaches this wrapper.
{ pkgs, ... }:
{
  emulator = pkgs.cemu;
  bin = "cemu";
  args = [
    "-g"
    "{target}"
  ];
}
