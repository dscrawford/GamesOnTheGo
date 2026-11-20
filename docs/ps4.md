# PS4

Played on [shadPS4](https://github.com/shadps4-emu/shadPS4) (`src/client/env/ps4.nix`).
It needs an **AVX2** CPU (Haswell / Zen or newer; a Steam Deck qualifies) and a Vulkan GPU.

## On the server

Put PS4 games anywhere the importer scans, in either form:

- a **fake PKG** (`*.pkg`), loose or in a release directory. It is published as
  is and unpacked on the client at install, with OpenOrbis `pkgtool`;
- an **extracted game**: a folder holding `eboot.bin` and `sce_sys/param.sfo`,
  published as its tree.

Either way the id comes from the game's own `param.sfo`: the region from the
content id's store prefix (`UP` usa, `EP` eur, `JP` jpn, else world) and the
slug from `TITLE`, or from the title id when the title has no Latin letters.
A PS3 `.pkg` (`\x7fPKG`) is never filed as PS4.

Updates (`gp`) and DLC (`ac`) are not attached yet: they land in the
importer's manual list.

## System modules

Some games need modules dumped from a console (`libSceNgs2.sprx` and the
like). Zip them as `/Games/ps4/sys_modules.zip`: every `*.sprx` in it, at any
depth, is put in the environment's `sys_modules/` on first launch. Without it
a launch warns once and goes on on shadPS4's own stubs.

## Installed

`~/Games/ps4/<id>/` is the extracted tree either way, and `eboot.bin` in it is
what launches (`data/overrides.json`, `"ps4/*"`). Saves are shadPS4's
`home/<user>/savedata/`.
