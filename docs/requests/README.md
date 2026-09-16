# Requests to padmap

GOTG is replacing its own controller handling with padmap
(`github:chadac/padmap`, branch `rustify`). Everything GOTG needs and padmap
does not yet do is written down here, one file per request, for whoever is
working on padmap to pick up.

Each file says what GOTG is trying to do, what it does today, why padmap's
current shape does not reach it, and what would be enough. None of them ask
for a redesign; they are the seams an *abstraction layer over emulators*
needs when the thing calling it keeps each emulator in a directory of its own.

| request | why |
| --- | --- |
| [emit-destinations.md](emit-destinations.md) | GOTG isolates every environment, so config must be written where it says |
| [dolphin.md](dolphin.md) | GameCube and Wii are Dolphin, and padmap writes no Dolphin config |
| [machine-readable-list.md](machine-readable-list.md) | a launcher has to enumerate pads without a daemon and without parsing prose |
