# Wii U — cemu. It wants the .rpx inside a decrypted title rather than the
# directory itself, which is what the `target` glob in data/overrides.json picks
# out before the ROM ever reaches this wrapper.
{ pkgs, ... }:
{
  emulator = pkgs.cemu;
  bin = "cemu";

  # Not generated bindings — Cemu's profile is a mapping table somebody made in
  # its settings screen, and it stays theirs. What the client writes is the one
  # flag that screen leaves off: <motion>, for a pad SDL says has a gyro. See
  # pads-cemu.sh.
  padEmulator = "cemu";
  args = [
    "-g"
    "{target}"
  ];
}
