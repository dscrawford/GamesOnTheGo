# Variants, ports, updates and the Deck

A variant is a file: `src/client/env/games/<platform>/<id>.<variant>.nix`,
beside `<id>.nix` for one game's settings and `<platform>.nix` for the rest.
One whose version range matches nothing installed is hidden from the picker.

```console
$ nix eval --raw gotg#n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando.gotgSpec.attr
env-n64-usa_legend_of_zelda_ocarina_of_time_rev2-rando
```

## Native ports

Not emulated: Ocarina of Time and Master Quest (Ship of Harkinian), Majora's
Mask (2 Ship 2 Harkinian), Donkey Kong 64 (recomp), Snowboard Kids 2 (recomp),
Super Smash Bros. (BattleShip), Pikmin (Open Nectar), Super Mario 64 `pc`
(sm64coopdx), Paper Mario `paperboat` (PaperBoat), Animal Crossing (ACGC PC
Port, Wine), Super Smash Bros. Melee (melee-pc).

A port that replaces the whole game has an `.emulate` attribute that puts it
back on the emulator -- a port is younger than what it replaces, and that is
how you find out which of the two has the bug. For the variants (`pc`,
`paperboat`) the plain attribute already is the emulator.

## Updates, from the picker

When the library is behind where it came from, or the picker and client
Steam starts are not what the library would build, a chip at the top right
says **Update available**; Select (or a click) runs `gotg update self` on the
loading screen -- the pin moved, both built, the two roots swapped, the picker
restarted into the new build with everybody's seats kept -- and nothing is
changed if any step fails.

A game whose build the library would now do differently, or whose install
lacks a release the catalog attached, wears an **!** instead of the download
arrow, and **Update** sits under Play in its menu (`gotg update
<platform>/<id>`). Behind it: `gotg update --check` (the network and the
evaluation, once per six hours) and `gotg complete updates` (the cached
answer).

A mod placed on the server by hand (`/Games/<platform>/mods/<id>/<release>/`,
a texture pack say) is such a release: the importer attaches it to the game,
the game installs as a directory with it beside the ROM, and the environment
reads it from there. PaperBoat's HD pack (MasterKillua's Refolded textures)
is one: `/Games/n64/mods/usa.paper_mario/<release>/`.

## On a Steam Deck

The Switch variants are the same names with a Deck profile: handheld 720p
unless a television is connected, the console's own 4 GiB, caps no higher
than 60 with UltraCam's DynamicFPS turning a miss into a dropped frame rather
than slow motion, and Paper Mario's 1080p mod left out. The launcher tells a
Deck by its firmware name (`GOTG_MACHINE=deck`) and a television by the
kernel's connectors (`GOTG_EXTERNAL_DISPLAY=1`); both can be set by hand.
Why: [research/switch-on-deck.md](research/switch-on-deck.md).
