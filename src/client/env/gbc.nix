# Game Boy Color — ares pinned to the colour console; see helpers.nix
# (aresPlatform, aresSystem, system) for the saves redirect, the pinning
# rationale, and why the save directory below is "Game Boy" and not "Game Boy
# Color". gb and gbc each get their own {state}, so sharing that name collides
# with nothing.
{ helpers, ... }:
helpers.aresPlatform {
  platform = "gbc";
  aresSystem = "Game Boy Color";
  system = "Game Boy";
  console = "GameBoyColor";
}
