# Mega Drive / Genesis — ares, with its saves redirected into this
# environment's own directory. See helpers.nix (aresPlatform, aresSystem,
# system) for the redirect and what the trailing slash is doing.
#
# One core claims .md and .gen, so unlike gb this needs no pinning to stop a
# prompt. It is named anyway, because that is what makes the save directory
# below a consequence rather than a guess.
#
# Both names read off a real launch: ares wrote "Mega Drive.sys" beside its
# settings and put the save in "{state}/saves/Mega Drive/".
{ helpers, ... }:
helpers.aresPlatform {
  platform = "genesis";
  aresSystem = "Mega Drive";
  system = "Mega Drive";
  console = "MegaDrive";
}
