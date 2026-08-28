# Game Boy — ares pinned to the mono console; see helpers.nix (aresPlatform,
# aresSystem, system) for the saves redirect, the pinning rationale, and the
# save-directory subtlety.
#
# A CGB-enhanced cart here — Pokémon Yellow is one — runs in mono rather than in
# the colour mode gbc would give it. The save already sitting in
# "{state}/saves/Game Boy/" assumes that, and the colour reading of such a game
# belongs in the gbc platform beside its own save.
{ helpers, ... }:
helpers.aresPlatform {
  platform = "gb";
  aresSystem = "Game Boy";
  system = "Game Boy";
  console = "GameBoy";
}
