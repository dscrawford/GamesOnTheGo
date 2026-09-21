# Requests to padmap

GOTG is replacing its own controller handling with padmap
(`github:dscrawford/padmap`, branch `main`). Everything GOTG needs and padmap
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
| [always-seating.md](always-seating.md) | a controller that arrives mid-game should be able to join without everybody stopping |
| [triton-assignment.md](triton-assignment.md) | **bug**: the 2026 Steam Controller pairs in a session and is then never read |
| [session-daemon.md](session-daemon.md) | every session starts unseated and the daemon ends with it — today it outlives everything and restores yesterday's seats |
