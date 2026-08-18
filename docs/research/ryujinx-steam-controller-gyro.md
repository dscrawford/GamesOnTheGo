# Steam Controller Gyro in Ryujinx: Research Report
*Generated: 2026-08-18 | Sources: ~25 | Confidence: High*

## Executive Summary

The premise "we need a server running to capture the gyro" turns out to be false for
Steam Controllers plugged into the machine running the emulator — and the repo's
committed motion support (`d185274`) is already the correct architecture. Ryujinx reads
motion natively from SDL (`motion_backend: GamepadDriver`), SDL's hidapi driver for the
current Steam Controller reports gyro + accelerometer, and GOTG both keeps that driver
enabled under the Steam launcher (`SDL_JOYSTICK_HIDAPI_STEAM=1`) and writes the motion
block per launch. A DSU/CemuHook server is needed only in two situations GOTG does not
currently hit: the Steam Deck's *built-in* gyro under Game Mode, and a pad held
exclusively by Steam Input. The obvious generic server, evdevhook2, would not have
helped: it explicitly does not support Steam hardware.

## 1. How Ryujinx consumes motion

- Two backends per controller entry, discriminated by `motion_backend`:
  `GamepadDriver` (native SDL sensors) or `CemuHook` (DSU UDP protocol). Verified
  against the live fork's source ([Ryubing mirror](https://lavaforge.org/preserve-emulation/Ryubing)).
- Native path: `SDL2Gamepad.cs` sets its Motion feature flag only when SDL reports
  **both** accelerometer and gyroscope, then reads both — no brand special-casing.
  This is the exact pair test `gotg-pads` replicates.
- Verified schema for the native block (what `pads-ryujinx.sh` writes):
  `{"motion_backend": "GamepadDriver", "sensitivity": 100, "gyro_deadzone": 1, "enable_motion": true}`
  — `StandardMotionConfigController` adds no other fields; 100/1/true are the GUI defaults.
- DSU block, if ever needed: adds `slot`, `alt_slot`, `mirror_input`,
  `dsu_server_host`, `dsu_server_port`; convention 127.0.0.1:26760 — the config file has
  no serializer default, both must be written explicitly
  ([Ryubing docs](https://docs.ryujinx.app/guides/setup-guide/)).
- Fork landscape: original Ryujinx shut down 2024-10; ryujinx-mirror is DMCA'd (HTTP 451);
  **Ryubing** is the live mainline and its motion config classes are unchanged. Ryubing
  is mid-migration to SDL3 ([MR !294](https://git.ryujinx.app/ryubing/ryujinx/-/merge_requests/294),
  merge status unconfirmed).

## 2. Why no server is needed for Steam Controllers

- SDL's hidapi drivers expose the Steam Controller's IMU directly: the current (2026)
  controller through the triton driver (the basis of the committed feature), and the
  original 2015 controller through `SDL_hidapi_steam.c` behind the same
  `SDL_JOYSTICK_HIDAPI_STEAM` hint GOTG already sets in every emulator environment.
- The servers would not help anyway:
  - **evdevhook2** (in nixpkgs, 1.0.2) supports only hid-nintendo/hid-playstation-class
    devices; Steam Controller support was closed "not planned"
    ([issue tracker](https://github.com/v1993/evdevhook2/issues?q=steam)).
  - The kernel's `hid-steam` sensor evdev node is **Deck-only** (Linux 6.10+,
    [Phoronix](https://www.phoronix.com/news/Linux-6.10-HID-Changes)), and the driver
    *removes* that node whenever a hidraw client (i.e. Steam) opens the device.

## 3. When a DSU server IS needed, and which one

| Situation | Server | Notes |
|---|---|---|
| Steam Deck built-in gyro, Game Mode | [SteamDeckGyroDSU](https://github.com/kmicki/SteamDeckGyroDSU) | Reads the Deck IMU over hidraw alongside Steam; UDP 26760; motion-only; SteamOS installer, **not in nixpkgs**; EmuDeck's answer for Ryujinx-on-Deck ([EmuDeck manual](https://manual.emudeck.com/tricks/ryujinx/)) |
| Steam Controller while Steam Input holds it exclusively | [steam-controller-dsu](https://lib.rs/crates/steam-controller-dsu) | Rust, active (v0.3.2, 2026-08); supports 2015 SC and 2026 SC incl. puck; port 26760; not in nixpkgs — would need packaging under `pkgs/` |
| Joy-Cons / DualSense etc. | [evdevhook2](https://github.com/v1993/evdevhook2) | Already in the pinned nixpkgs; zero-config; 4 DSU slots per instance |

- Steam Input itself can never substitute: the Steam Virtual Gamepad exposes no raw
  sensor channel ([SDL #9148](https://github.com/libsdl-org/SDL/issues/9148)); gyro
  through Steam reaches games only as mouse/stick mappings.
- Only one server can bind 26760; co-located setups use 127.0.0.1.

## 4. State of the repo against these findings

Committed in `d185274` and consistent with everything above:

- `gotg-pads` asks SDL the same gyro+accel pair question Ryujinx asks.
- `pads_ryujinx_motion` writes the exact verified `GamepadDriver` block, matches pads
  to entries by Ryujinx's truncated-name format, and leaves player-configured
  `CemuHook` blocks alone — the right escape hatch given §3.
- `SDL_JOYSTICK_HIDAPI_STEAM=1` in every env (`lib.nix` baseEnv) keeps the SC driver
  alive under the launcher's global `SDL_JOYSTICK_HIDAPI=0`.
- Cemu gets the same treatment via `<motion>` in the controller profile.

**Verified this session:** full bats suite green through the `client-tests` flake
check; Ryujinx schema field-for-field against Ryubing source. **Not verified:** live
hardware pass (no Steam Controller was attached to this machine during the session) —
worth one `gotg controllers list` with the pad awake to see `motion: gyro and
accelerometer` reported.

**Deliberately not built:** a DSU server path. For GOTG's supported hardware today it
would be dead code; if a Steam Deck (built-in gyro) or Steam-Input-exclusive setup
enters the picture, package `steam-controller-dsu`/`SteamDeckGyroDSU` and write the
`CemuHook` block documented in §1 with explicit host/port 127.0.0.1:26760.

## Sources

1. [Ryubing source mirror](https://lavaforge.org/preserve-emulation/Ryubing) — motion config classes, SDL2Gamepad.cs
2. [Ryubing setup docs](https://docs.ryujinx.app/guides/setup-guide/) — DSU conventions, backend choice
3. [ryujinx.io input guide](https://ryujinx.io/input/) — native motion support matrix
4. [EmuDeck Ryujinx manual](https://manual.emudeck.com/tricks/ryujinx/) — Deck gyro needs SteamDeckGyroDSU; disable Steam Input for native
5. [SteamDeckGyroDSU](https://github.com/kmicki/SteamDeckGyroDSU) — port 26760, motion-only, SteamOS service
6. [evdevhook2](https://github.com/v1993/evdevhook2) + issue tracker — no Steam hardware, "not planned"
7. [steam-controller-dsu](https://lib.rs/crates/steam-controller-dsu) — DSU for 2015/2026 Steam Controllers
8. [Linux 6.10 hid-steam IMU](https://www.phoronix.com/news/Linux-6.10-HID-Changes) + [LKML patch](https://lkml.iu.edu/hypermail/linux/kernel/2404.0/07946.html) — Deck-only evdev sensors, yanked on hidraw open
9. [SDL evdev sensor support PR #7697](https://github.com/libsdl-org/SDL/pull/7697); [SDL #9148](https://github.com/libsdl-org/SDL/issues/9148) — virtual gamepad has no sensors
10. [nixpkgs #230927](https://github.com/NixOS/nixpkgs/issues/230927) — historical SDL-motion packaging pitfall
11. [Nintendo DMCA 2025-03-06](https://github.com/github/dmca/blob/master/2025/03/2025-03-06-nintendo.md) — fork landscape

## Methodology

Two parallel research agents (Ryujinx motion architecture/config schema; Linux DSU
servers and Steam hardware kernel/SDL support) over ~14 searches and ~15 deep-read
sources, cross-checked against the repo's committed implementation and the pinned
nixpkgs package set. Live hardware probe attempted (no controller attached).
